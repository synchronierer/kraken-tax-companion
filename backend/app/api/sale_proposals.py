from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, TypeAdapter, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.tax import tax_inputs
from app.config.settings import Settings, get_settings
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
from app.infrastructure.kraken_market import (
    KrakenMarketError,
    KrakenMarketQuote,
    KrakenPublicMarketClient,
)
from app.infrastructure.kraken_private import (
    ExchangeBalanceSnapshot,
    KrakenPrivateClient,
    KrakenPrivateError,
)

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


class LiveSaleSimulationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, allow_inf_nan=False)

    asset: str
    mode: SaleMode
    quantity: Decimal | None = None
    target_eur: Decimal | None = None
    estimated_fee_eur: Decimal | None = None

    @field_validator("asset")
    @classmethod
    def validate_asset(cls, value: str) -> str:
        if not value:
            raise ValueError("asset must not be empty")
        return value.upper()


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


def build_balance_client(settings: Settings) -> KrakenPrivateClient:
    return KrakenPrivateClient(
        api_key=settings.kraken_api_key or "",
        api_secret=settings.kraken_api_secret or "",
        base_url=settings.kraken_api_base_url,
        timeout=settings.kraken_api_timeout,
        max_retries=settings.kraken_api_max_retries,
        ledger_min_interval_seconds=settings.kraken_ledger_min_interval_seconds,
        rate_limit_retry_base_seconds=settings.kraken_rate_limit_retry_base_seconds,
    )


def build_market_client(settings: Settings) -> KrakenPublicMarketClient:
    return KrakenPublicMarketClient(
        base_url=settings.kraken_api_base_url,
        timeout=settings.kraken_api_timeout,
    )


def _balance_values(
    snapshot: ExchangeBalanceSnapshot, asset: str
) -> tuple[Decimal, Decimal, Decimal, list[dict[str, Any]], list[str]]:
    matching = [item for item in snapshot.balances if item.canonical_asset == asset]
    total = exact_decimal_sum(tuple(item.balance for item in matching))
    spot = exact_decimal_sum(
        tuple(item.calculated_available for item in matching if item.extension is None)
    )
    non_spot = exact_decimal_sum(
        tuple(item.balance for item in matching if item.extension is not None)
    )
    details = [
        {
            "provider_asset_code": item.provider_asset_code,
            "canonical_asset": item.canonical_asset,
            "balance": str(item.balance),
            "credit": str(item.credit),
            "credit_used": str(item.credit_used),
            "hold_trade": str(item.hold_trade),
            "calculated_available": str(item.calculated_available),
            "extension": item.extension,
            "balance_kind": item.balance_kind,
            "provider_metadata": dict(item.provider_metadata),
        }
        for item in snapshot.balances
        if item.canonical_asset == asset or item.canonical_asset is None
    ]
    return total, spot, non_spot, details, list(snapshot.warnings)


def _quote_values(quote: KrakenMarketQuote, now: datetime) -> dict[str, Any]:
    return {
        "price_available": True,
        "reference_price_eur": str(quote.selected_reference_price_eur),
        "price_source": quote.selected_reference,
        "price_timestamp": quote.fetched_at,
        "price_age_seconds": max(0, int((now - quote.fetched_at).total_seconds())),
        "pair": quote.pair,
        "quote_asset": quote.quote_asset,
        "best_bid_eur": str(quote.best_bid_eur),
        "best_ask_eur": str(quote.best_ask_eur),
        "last_trade_eur": str(quote.last_trade_eur),
        "execution_price_guaranteed": False,
    }


def _live_context_data(
    *, asset: str, db: Session, settings: Settings, now: datetime
) -> dict[str, Any]:
    lots, totals, known_assets, incomplete = _inventory_snapshot(db, as_of=now)
    if asset not in known_assets:
        raise _error(404, "UNKNOWN_ASSET", "Das Asset ist im Steuerbestand unbekannt.")
    tax_status, tax_warnings, restricted = _tax_context(db)
    warnings = list(tax_warnings)
    blocked_reasons: list[str] = []
    if asset in restricted:
        blocked_reasons.append("UNRESOLVED_FINANCIAL_TAX_MAPPING")
    if asset in incomplete:
        blocked_reasons.append("INCOMPLETE_ASSET_VALUATION")
    inventory = totals.get(asset, Decimal("0"))
    balance_available = False
    balance_permission_available = False
    balance_error_code: str | None = None
    total: Decimal | None = None
    spot: Decimal | None = None
    non_spot: Decimal | None = None
    details: list[dict[str, Any]] = []
    try:
        snapshot = build_balance_client(settings).extended_balance()
        total, spot, non_spot, details, balance_warnings = _balance_values(
            snapshot, asset
        )
        warnings.extend(balance_warnings)
        balance_available = True
        balance_permission_available = True
    except KrakenPrivateError as error:
        balance_error_code = error.code
        warnings.append(
            {
                "kraken_not_configured": "KRAKEN_API_KEY_NOT_CONFIGURED",
                "kraken_balance_permission_missing": (
                    "KRAKEN_BALANCE_PERMISSION_MISSING"
                ),
                "kraken_authentication_failed": "KRAKEN_AUTHENTICATION_FAILED",
                "kraken_rate_limited": "KRAKEN_RATE_LIMITED",
                "kraken_invalid_response": "KRAKEN_BALANCE_INVALID_RESPONSE",
            }.get(error.code, "EXCHANGE_BALANCE_UNAVAILABLE")
        )
        warnings.append("EXCHANGE_BALANCE_UNAVAILABLE")
    quote: KrakenMarketQuote | None = None
    price_error_code: str | None = None
    try:
        quote = build_market_client(settings).eur_quote(asset)
    except KrakenMarketError as error:
        price_error_code = error.code
        warnings.append(
            "KRAKEN_EUR_PAIR_UNAVAILABLE"
            if error.code == "kraken_eur_pair_unavailable"
            else "KRAKEN_PRICE_UNAVAILABLE"
        )
    difference = (
        exact_decimal_sum((inventory, total.copy_negate()))
        if total is not None
        else None
    )
    reconciliation = "UNKNOWN"
    if difference is not None:
        reconciliation = "MATCH" if difference == 0 else "DIFFERENCE"
        if difference != 0:
            warnings.append("EXCHANGE_INVENTORY_DIFFERENCE")
    result: dict[str, Any] = {
        "asset": asset,
        "inventory_quantity": str(inventory),
        "exchange_balance_available": balance_available,
        "balance_permission_available": balance_permission_available,
        "exchange_total_balance": str(total) if total is not None else None,
        "exchange_spot_available_quantity": str(spot) if spot is not None else None,
        "exchange_non_spot_balance": str(non_spot) if non_spot is not None else None,
        "inventory_exchange_difference": (
            str(difference) if difference is not None else None
        ),
        "difference_quantity": str(difference) if difference is not None else None,
        "reconciliation_status": reconciliation,
        "balance_details": details,
        "blocked_reasons": blocked_reasons,
        "tax_data_status": tax_status,
        "warnings": list(dict.fromkeys(warnings)),
        "balance_error_code": balance_error_code,
        "price_error_code": price_error_code,
        "price_available": False,
        "reference_price_eur": None,
        "price_source": "KRAKEN_BEST_BID",
        "price_timestamp": None,
        "price_age_seconds": None,
        "pair": None,
        "quote_asset": "EUR",
        "best_bid_eur": None,
        "best_ask_eur": None,
        "last_trade_eur": None,
        "execution_price_guaranteed": False,
        "safe_simulatable_quantity": (
            str(min(inventory, max(spot, Decimal("0")))) if spot is not None else None
        ),
        "_lots": lots,
    }
    if quote is not None:
        result.update(_quote_values(quote, now))
    return result


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


@router.get("/live-context")
def live_context(
    asset: Annotated[
        str, Query(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9]+$")
    ],
    db: Db,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    result = _live_context_data(
        asset=asset.upper(), db=db, settings=settings, now=utc_now()
    )
    result.pop("_lots")
    return result


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


@router.post("/simulate-live")
def simulate_live(
    data: LiveSaleSimulationInput,
    db: Db,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    now = utc_now()
    context = _live_context_data(asset=data.asset, db=db, settings=settings, now=now)
    if context["blocked_reasons"]:
        code = str(context["blocked_reasons"][0])
        raise _error(409, code, "Das Asset ist für die Simulation gesperrt.")
    if not context["exchange_balance_available"]:
        raise _error(
            503,
            "EXCHANGE_BALANCE_UNAVAILABLE",
            "Der verfügbare Kraken-Spot-Bestand konnte nicht gelesen werden.",
        )
    if context["reconciliation_status"] != "MATCH":
        raise _error(
            409,
            "EXCHANGE_INVENTORY_NOT_RECONCILED",
            "Steuerbestand und Kraken-Gesamtbestand stimmen nicht überein.",
        )
    if not context["price_available"]:
        code = (
            "KRAKEN_EUR_PAIR_UNAVAILABLE"
            if context["price_error_code"] == "kraken_eur_pair_unavailable"
            else "KRAKEN_PRICE_UNAVAILABLE"
        )
        raise _error(503, code, "Der Kraken-EUR-Referenzpreis ist nicht verfügbar.")
    spot = Decimal(str(context["exchange_spot_available_quantity"]))
    price = Decimal(str(context["reference_price_eur"]))
    try:
        request = SaleProposalRequest(
            asset=data.asset,
            mode=data.mode,
            quantity=data.quantity,
            target_eur=data.target_eur,
            estimated_fee_eur=data.estimated_fee_eur,
            reference_price=ReferencePrice(
                price_eur=price,
                source="KRAKEN_BEST_BID",
                timestamp=cast(datetime, context["price_timestamp"]),
            ),
        )
        result = simulate_sale(
            simulation_id=uuid4(),
            request=request,
            lots=cast(list[SaleInventoryLot], context["_lots"]),
            now=now,
            tax_data_status=str(context["tax_data_status"]),
            tax_warnings=tuple(cast(list[str], context["warnings"])),
            exchange_available_quantity=spot,
            exchange_reconciled=True,
        )
    except (SaleProposalError, ValueError, InvalidOperation) as error:
        code = getattr(error, "code", "INVALID_SALE_PROPOSAL")
        raise _error(
            (
                409
                if code
                in {
                    "INSUFFICIENT_FIFO_INVENTORY",
                    "INSUFFICIENT_EXCHANGE_AVAILABLE_BALANCE",
                }
                else 422
            ),
            code,
            str(error),
        ) from error
    response = cast(
        dict[str, Any], SALE_SIMULATION_ADAPTER.dump_python(result, mode="json")
    )
    response.update(
        {
            key: context[key]
            for key in (
                "exchange_total_balance",
                "exchange_non_spot_balance",
                "inventory_exchange_difference",
                "reconciliation_status",
                "safe_simulatable_quantity",
                "pair",
                "best_bid_eur",
                "best_ask_eur",
                "last_trade_eur",
            )
        }
    )
    return response
