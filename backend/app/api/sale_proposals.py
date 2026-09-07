from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, TypeAdapter, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.tax import tax_inputs
from app.core.financial_review import (
    FinancialReviewResolution,
    TaxMappingStatus,
)
from app.core.sale_planner import (
    ReferencePrice,
    SaleInventoryLot,
    SaleMode,
    SaleProposalError,
    SaleProposalRequest,
    SaleSimulation,
    simulate_sale,
)
from app.core.tax import (
    NON_INVENTORY_ASSETS,
    TaxReportingPeriod,
    TaxReviewDecision,
    TaxRuleVersion,
    calculate_fifo,
    effective_tax_review_decisions,
)
from app.core.time import utc_now
from app.core.transformation import AcquisitionLot, DisposalEvent
from app.core.valuation import (
    FeeTaxClassification,
    FeeTaxReviewStatus,
    ValuationDecision,
    ValuationDecisionStatus,
    exact_decimal_sum,
)
from app.database.session import get_session

router = APIRouter(prefix="/api/sale-proposals", tags=["sale-proposals"])
Db = Annotated[Session, Depends(get_session)]
SALE_SIMULATION_ADAPTER = TypeAdapter(SaleSimulation)


class SaleSimulationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, allow_inf_nan=False)

    asset: str
    mode: SaleMode
    quantity: Decimal | None = None
    target_eur: Decimal | None = None
    reference_price_eur: Decimal
    price_timestamp: datetime | None = None
    estimated_fee_eur: Decimal | None = None

    @field_validator("asset")
    @classmethod
    def validate_asset(cls, value: str) -> str:
        if not value:
            raise ValueError("asset must not be empty")
        return value.upper()

    @field_validator("quantity", "target_eur", "reference_price_eur")
    @classmethod
    def validate_positive(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value <= 0:
            raise ValueError("value must be greater than zero")
        return value

    @field_validator("estimated_fee_eur")
    @classmethod
    def validate_fee(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value < 0:
            raise ValueError("estimated_fee_eur must not be negative")
        return value


def _latest_valuations(db: Session) -> dict[UUID, ValuationDecision]:
    result: dict[UUID, ValuationDecision] = {}
    for item in db.scalars(select(ValuationDecision)):
        current = result.get(item.domain_object_id)
        if current is None or item.version > current.version:
            result[item.domain_object_id] = item
    return result


def _inventory_snapshot(
    db: Session, *, as_of: datetime
) -> tuple[list[SaleInventoryLot], dict[str, Decimal], set[str], set[str]]:
    period = TaxReportingPeriod(start=date.min, end=as_of.date())
    acquisitions, disposals, missing = tax_inputs(db, period)
    acquisitions = [item for item in acquisitions if item.acquired_at <= as_of]
    disposals = [item for item in disposals if item.disposed_at <= as_of]
    fifo = calculate_fifo(
        run_id=uuid4(),
        period=period,
        rules=TaxRuleVersion(),
        acquisitions=acquisitions,
        disposals=disposals,
    )
    acquisition_lots = [
        item
        for item in db.scalars(select(AcquisitionLot))
        if item.occurred_at <= as_of and item.asset_code not in NON_INVENTORY_ASSETS
    ]
    disposal_events = [
        item
        for item in db.scalars(select(DisposalEvent))
        if item.occurred_at <= as_of and item.asset_code not in NON_INVENTORY_ASSETS
    ]
    known_assets = {item.asset_code.upper() for item in acquisition_lots}
    quantity_balances: dict[str, Decimal] = {}
    for acquisition in acquisition_lots:
        asset = acquisition.asset_code.upper()
        quantity_balances[asset] = exact_decimal_sum(
            (quantity_balances.get(asset, Decimal("0")), acquisition.quantity)
        )
    for disposal in disposal_events:
        asset = disposal.asset_code.upper()
        quantity_balances[asset] = exact_decimal_sum(
            (
                quantity_balances.get(asset, Decimal("0")),
                disposal.quantity.copy_negate(),
            )
        )
    incomplete = {
        item.asset_code.upper()
        for item in missing
        if item.code == "tax_valuation_missing"
        and item.source_type in {"AcquisitionLot", "DisposalEvent"}
        and item.occurred_at <= as_of
    }
    insufficient_disposals = {item.source_object_id for item in fifo.reviews}
    incomplete.update(
        item.asset_code
        for item in disposals
        if item.disposal_id in insufficient_disposals
    )
    snapshots = [
        SaleInventoryLot(
            inventory_lot_id=item.id,
            acquisition_lot_id=item.acquisition_lot_id,
            valuation_decision_id=item.valuation_decision_id,
            asset=item.asset_code,
            remaining_quantity=item.remaining_quantity,
            remaining_cost_eur=item.remaining_cost_eur,
            acquired_at=item.acquired_at,
            sequence=item.sequence,
        )
        for item in fifo.lots
        if item.remaining_quantity > 0
    ]
    totals: dict[str, Decimal] = {}
    for item in snapshots:
        totals[item.asset] = exact_decimal_sum(
            (totals.get(item.asset, Decimal("0")), item.remaining_quantity)
        )
    for asset in incomplete:
        quantity = quantity_balances.get(asset, Decimal("0"))
        if quantity > 0:
            totals[asset] = quantity
    return snapshots, totals, known_assets, incomplete


def _pending_resolutions(
    db: Session,
) -> tuple[int, int, set[str]]:
    pending_count = 0
    withdrawal_count = 0
    restricted_assets: set[str] = set()
    for item in db.scalars(select(FinancialReviewResolution)):
        if item.tax_mapping_status is TaxMappingStatus.PENDING:
            pending_count += 1
            value = item.metadata.get("disposed_asset")
            if isinstance(value, str) and value.strip():
                restricted_assets.add(value.strip().upper())
        if item.metadata.get("fee_tax_status") == "REVIEW_REQUIRED":
            withdrawal_count += 1
    return pending_count, withdrawal_count, restricted_assets


def _tax_context(
    db: Session,
) -> tuple[str, tuple[str, ...], set[str]]:
    effective = effective_tax_review_decisions(
        list(db.scalars(select(TaxReviewDecision)))
    )
    current_decisions = _latest_valuations(db).values()
    staking_open = sum(
        1
        for decision in current_decisions
        if decision.status is ValuationDecisionStatus.RESOLVED
        and decision.fee_tax_classification
        is FeeTaxClassification.WERBUNGSKOSTEN_CANDIDATE
        and decision.fee_tax_review_status is FeeTaxReviewStatus.REVIEW_REQUIRED
        and decision.id not in effective
    )
    pending_count, withdrawal_count, restricted = _pending_resolutions(db)
    warnings: list[str] = []
    if staking_open:
        warnings.append(f"OPEN_STAKING_PLATFORM_FEE_REVIEWS:{staking_open}")
    if pending_count:
        warnings.append(f"PENDING_FINANCIAL_TAX_MAPPINGS:{pending_count}")
    if withdrawal_count:
        warnings.append(f"OPEN_WITHDRAWAL_FEE_TAX_REVIEWS:{withdrawal_count}")
    return ("PARTIAL" if warnings else "COMPLETE"), tuple(warnings), restricted


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


@router.get("/inventory")
def sale_inventory(db: Db) -> dict[str, Any]:
    _, totals, _, incomplete = _inventory_snapshot(db, as_of=utc_now())
    tax_status, warnings, restricted = _tax_context(db)
    return {
        "items": [
            {
                "asset": asset,
                "inventory_quantity": str(quantity),
                "exchange_available_quantity": None,
                "blocked": asset in restricted or asset in incomplete,
                "blocked_reasons": [
                    *(
                        ["UNRESOLVED_FINANCIAL_TAX_MAPPING"]
                        if asset in restricted
                        else []
                    ),
                    *(["INCOMPLETE_ASSET_VALUATION"] if asset in incomplete else []),
                ],
            }
            for asset, quantity in sorted(totals.items())
        ],
        "tax_data_status": tax_status,
        "warnings": ["EXCHANGE_BALANCE_NOT_RECONCILED", *warnings],
    }


@router.post("/simulate")
def simulate(data: SaleSimulationInput, db: Db) -> dict[str, Any]:
    if data.asset == "EUR":
        raise _error(422, "EUR_CRYPTO_SALE_NOT_ALLOWED", "EUR ist kein Crypto-Sale.")
    now = utc_now()
    lots, _, known_assets, incomplete = _inventory_snapshot(db, as_of=now)
    if data.asset not in known_assets:
        raise _error(404, "UNKNOWN_ASSET", "Das Asset ist im Steuerbestand unbekannt.")
    tax_status, warnings, restricted = _tax_context(db)
    if data.asset in restricted:
        raise _error(
            409,
            "UNRESOLVED_FINANCIAL_TAX_MAPPING",
            "Das Asset ist von einem offenen Financial-Tax-Mapping betroffen.",
        )
    if data.asset in incomplete:
        raise _error(
            409,
            "INCOMPLETE_ASSET_VALUATION",
            "Für das Asset liegt keine vollständige aktuelle Bewertung vor.",
        )
    try:
        request = SaleProposalRequest(
            asset=data.asset,
            mode=data.mode,
            quantity=data.quantity,
            target_eur=data.target_eur,
            estimated_fee_eur=data.estimated_fee_eur,
            reference_price=ReferencePrice(
                price_eur=data.reference_price_eur,
                source="MANUAL_SIMULATION",
                timestamp=data.price_timestamp or now,
            ),
        )
        result = simulate_sale(
            simulation_id=uuid4(),
            request=request,
            lots=lots,
            now=now,
            tax_data_status=tax_status,
            tax_warnings=warnings,
        )
    except (SaleProposalError, ValueError, InvalidOperation) as error:
        code = getattr(error, "code", "INVALID_SALE_PROPOSAL")
        raise _error(
            422 if code != "INSUFFICIENT_FIFO_INVENTORY" else 409, code, str(error)
        ) from error
    return cast(
        dict[str, Any], SALE_SIMULATION_ADAPTER.dump_python(result, mode="json")
    )
