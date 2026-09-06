from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, TypeAdapter, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

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
    InventoryLot,
    TaxCalculationRun,
    TaxReviewCase,
    TaxReviewDecision,
    TaxRunStatus,
    effective_tax_review_decisions,
)
from app.core.time import utc_now
from app.core.transformation import AcquisitionLot
from app.core.valuation import (
    FeeTaxReviewStatus,
    ValuationDecision,
    ValuationDecisionStatus,
    exact_decimal_multiply,
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


def _latest_tax_run(db: Session) -> TaxCalculationRun | None:
    completed = {
        TaxRunStatus.COMPLETED,
        TaxRunStatus.COMPLETED_WITH_REVIEW,
    }
    runs = [
        item
        for item in db.scalars(select(TaxCalculationRun))
        if item.status in completed
    ]
    if not runs:
        return None
    return max(runs, key=lambda item: (item.started_at, item.id.hex))


def _latest_valuations(db: Session) -> dict[UUID, ValuationDecision]:
    result: dict[UUID, ValuationDecision] = {}
    for item in db.scalars(select(ValuationDecision)):
        current = result.get(item.domain_object_id)
        if current is None or item.version > current.version:
            result[item.domain_object_id] = item
    return result


def _proportional_remaining_cost(
    inventory: InventoryLot, decision: ValuationDecision
) -> Decimal:
    full_cost = exact_decimal_sum(
        (
            decision.net_acquisition_value_eur or decision.eur_value,
            inventory.acquisition_fee_eur,
        )
    )
    if inventory.remaining_quantity == inventory.original_quantity:
        return full_cost
    with localcontext() as context:
        context.prec = 80
        return (
            exact_decimal_multiply(full_cost, inventory.remaining_quantity)
            / inventory.original_quantity
        )


def _inventory_snapshot(
    db: Session,
) -> tuple[list[SaleInventoryLot], dict[str, Decimal], set[str], set[str]]:
    known_assets = {
        item.asset_code.upper() for item in db.scalars(select(AcquisitionLot))
    }
    run = _latest_tax_run(db)
    if run is None:
        return [], {}, known_assets, set()
    valuations = _latest_valuations(db)
    snapshots: list[SaleInventoryLot] = []
    incomplete: set[str] = set()
    inventory = [
        item
        for item in db.scalars(select(InventoryLot))
        if item.tax_calculation_run_id == run.id and item.remaining_quantity > 0
    ]
    totals: dict[str, Decimal] = {}
    for item in inventory:
        asset = item.asset_code.upper()
        totals[asset] = exact_decimal_sum(
            (totals.get(asset, Decimal("0")), item.remaining_quantity)
        )
        decision = valuations.get(item.acquisition_lot_id)
        if decision is None or decision.status is not ValuationDecisionStatus.RESOLVED:
            incomplete.add(asset)
            continue
        snapshots.append(
            SaleInventoryLot(
                inventory_lot_id=item.id,
                acquisition_lot_id=item.acquisition_lot_id,
                valuation_decision_id=decision.id,
                asset=item.asset_code,
                remaining_quantity=item.remaining_quantity,
                remaining_cost_eur=_proportional_remaining_cost(item, decision),
                acquired_at=item.acquired_at,
                sequence=item.sequence,
            )
        )
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
    run = _latest_tax_run(db)
    effective = effective_tax_review_decisions(
        list(db.scalars(select(TaxReviewDecision)))
    )
    staking_open = 0
    if run is not None:
        cases = [
            item
            for item in db.scalars(select(TaxReviewCase))
            if item.tax_calculation_run_id == run.id
            and item.code == "tax_staking_platform_fee_candidate_review"
        ]
        valuation_ids = {
            item.source_object_id
            for item in cases
            if item.source_object_type == "ValuationDecision"
        }
        decisions = {
            item.id: item
            for item in db.scalars(select(ValuationDecision))
            if item.id in valuation_ids
        }
        staking_open = sum(
            1
            for item in cases
            if item.source_object_id not in effective
            and (decision := decisions.get(item.source_object_id)) is not None
            and decision.fee_tax_review_status is FeeTaxReviewStatus.REVIEW_REQUIRED
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
    _, totals, _, incomplete = _inventory_snapshot(db)
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
    lots, _, known_assets, incomplete = _inventory_snapshot(db)
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
    now = utc_now()
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
