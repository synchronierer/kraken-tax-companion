from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, getcontext, localcontext
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import sale_proposals as sale_proposals_api
from app.api.sale_proposals import sale_inventory
from app.config.settings import Settings
from app.core.financial_review import (
    FinancialReviewResolution,
    FinancialReviewType,
    ResolutionStatus,
    ReviewConfidence,
    TaxMappingStatus,
)
from app.core.sale_planner import (
    FixedReferencePriceSource,
    HoldingPeriodStatus,
    ReferencePrice,
    SaleInventoryLot,
    SaleMode,
    SaleProposalError,
    SaleProposalRequest,
    simulate_sale,
)
from app.core.tax import (
    DisposalCalculation,
    InventoryLot,
    LotAllocation,
    TaxCalculationRun,
    TaxJournalEntry,
    TaxReviewCase,
    TaxReviewDecision,
    TaxReviewDecisionValue,
    TaxRunStatus,
)
from app.core.transformation import (
    AcquisitionLot,
    AcquisitionType,
    DisposalEvent,
    DisposalType,
    TaxTreatmentHint,
    TransformationRun,
    TransformationStatus,
    ValuationMethod,
    ValuationRequirement,
    ValuationStatus,
)
from app.core.valuation import (
    FeeTaxClassification,
    FeeTaxReviewStatus,
    PriceMethod,
    ValuationDecision,
    ValuationDecisionStatus,
    ValuationRun,
    ValuationRunStatus,
    exact_decimal_sum,
)
from app.database.base import Base
from app.database.session import get_session
from app.infrastructure.kraken_market import KrakenMarketError, KrakenMarketQuote
from app.infrastructure.kraken_private import (
    ExchangeBalanceSnapshot,
    KrakenExtendedBalance,
    KrakenPrivateError,
)
from app.main import app

NOW = datetime(2026, 9, 6, 12, tzinfo=UTC)
PENDING_RESOLUTION_ID = UUID("64391247-2057-4f0c-b9e7-4b07c163ad65")


def inventory_lot(
    quantity: str,
    cost: str,
    acquired_at: datetime,
    *,
    asset: str = "ETH",
    sequence: int = 0,
) -> SaleInventoryLot:
    return SaleInventoryLot(
        inventory_lot_id=uuid4(),
        acquisition_lot_id=uuid4(),
        valuation_decision_id=uuid4(),
        asset=asset,
        remaining_quantity=Decimal(quantity),
        remaining_cost_eur=Decimal(cost),
        acquired_at=acquired_at,
        sequence=sequence,
    )


def proposal(
    mode: SaleMode,
    *,
    quantity: str | None = None,
    target_eur: str | None = None,
    price: str = "2000",
    price_at: datetime = NOW,
    fee: str | None = None,
) -> SaleProposalRequest:
    return SaleProposalRequest(
        asset="eth",
        mode=mode,
        quantity=Decimal(quantity) if quantity is not None else None,
        target_eur=Decimal(target_eur) if target_eur is not None else None,
        estimated_fee_eur=Decimal(fee) if fee is not None else None,
        reference_price=ReferencePrice(
            price_eur=Decimal(price),
            source="FAKE_TEST",
            timestamp=price_at,
        ),
    )


def test_quantity_fifo_is_exact_read_only_and_reports_holding_periods() -> None:
    first = inventory_lot("1", "1000", NOW - timedelta(days=366), sequence=1)
    second = inventory_lot("2", "3000", NOW - timedelta(days=100), sequence=2)
    third = inventory_lot("1", "2500", NOW - timedelta(days=10), sequence=3)
    lots = [second, first, third]
    context = getcontext()
    context_before: dict[str, object] = {
        "prec": context.prec,
        "rounding": context.rounding,
        "Emin": context.Emin,
        "Emax": context.Emax,
        "capitals": context.capitals,
        "clamp": context.clamp,
        "flags": context.flags.copy(),
        "traps": context.traps.copy(),
    }

    result = simulate_sale(
        simulation_id=uuid4(),
        request=proposal(SaleMode.QUANTITY, quantity="1.5", fee="3"),
        lots=lots,
        now=NOW,
        tax_data_status="PARTIAL",
        tax_warnings=("OPEN_STAKING_PLATFORM_FEE_REVIEWS:380",),
    )

    assert result.proposed_quantity == Decimal("1.5")
    assert result.estimated_gross_proceeds_eur == Decimal("3000.0")
    assert result.estimated_net_proceeds_eur == Decimal("2997.0")
    assert result.acquisition_cost_eur == Decimal("1750.0")
    assert result.estimated_gain_loss_eur == Decimal("1247.0")
    assert [item.quantity for item in result.fifo_allocations] == [
        Decimal("1"),
        Decimal("0.5"),
    ]
    assert (
        exact_decimal_sum(
            tuple(item.simulated_proceeds_eur for item in result.fifo_allocations)
        )
        == result.estimated_gross_proceeds_eur
    )
    assert (
        exact_decimal_sum(
            tuple(
                item.simulated_fee_eur or Decimal("0")
                for item in result.fifo_allocations
            )
        )
        == result.estimated_fee_eur
    )
    assert [item.holding_period_status for item in result.fifo_allocations] == [
        HoldingPeriodStatus.OVER_ONE_YEAR,
        HoldingPeriodStatus.WITHIN_ONE_YEAR,
    ]
    assert result.fifo_allocations[0].reached_one_year is True
    assert result.fifo_allocations[1].reached_one_year is False
    assert all(item.hypothetical_disposed_at == NOW for item in result.fifo_allocations)
    assert result.earliest_acquired_at == first.acquired_at
    assert result.latest_acquired_at == second.acquired_at
    assert result.exchange_available_quantity is None
    assert result.available_inventory_quantity == Decimal("4")
    assert result.execution_price_guaranteed is False
    assert result.dry_run is True
    assert result.order_created is False
    assert result.exchange_mutated is False
    assert result.tax_run_created is False
    assert "EXCHANGE_BALANCE_NOT_RECONCILED" in result.warnings
    assert "OPEN_STAKING_PLATFORM_FEE_REVIEWS:380" in result.warnings
    assert "ESTIMATED_FEE_UNKNOWN" not in result.warnings
    assert lots == [second, first, third]
    context = getcontext()
    context_after: dict[str, object] = {
        "prec": context.prec,
        "rounding": context.rounding,
        "Emin": context.Emin,
        "Emax": context.Emax,
        "capitals": context.capitals,
        "clamp": context.clamp,
        "flags": context.flags.copy(),
        "traps": context.traps.copy(),
    }
    assert context_after == context_before


def test_target_all_inventory_unknown_fee_and_stale_price() -> None:
    lots = [
        inventory_lot("0.2", "100", NOW - timedelta(days=10)),
        inventory_lot("0.3", "180", NOW - timedelta(days=9), sequence=1),
    ]
    target = simulate_sale(
        simulation_id=uuid4(),
        request=proposal(
            SaleMode.TARGET_EUR,
            target_eur="100",
            price="300",
            price_at=NOW - timedelta(seconds=301),
        ),
        lots=lots,
        now=NOW,
        tax_data_status="COMPLETE",
    )
    with localcontext() as context:
        context.prec = 80
        context.rounding = ROUND_DOWN
        expected_quantity = Decimal("100") / Decimal("300")
    assert target.proposed_quantity == expected_quantity
    assert target.estimated_gross_proceeds_eur <= Decimal("100")
    assert target.estimated_fee_eur is None
    assert target.estimated_net_proceeds_eur == target.estimated_gross_proceeds_eur
    assert "ESTIMATED_FEE_UNKNOWN" in target.warnings
    assert "REFERENCE_PRICE_STALE" in target.warnings

    complete = simulate_sale(
        simulation_id=uuid4(),
        request=proposal(SaleMode.ALL_AVAILABLE_INVENTORY),
        lots=lots,
        now=NOW,
        tax_data_status="COMPLETE",
    )
    assert complete.proposed_quantity == Decimal("0.5")
    assert [item.quantity for item in complete.fifo_allocations] == [
        Decimal("0.2"),
        Decimal("0.3"),
    ]
    assert complete.acquisition_cost_eur == Decimal("280")
    exact_quantity = simulate_sale(
        simulation_id=uuid4(),
        request=proposal(SaleMode.QUANTITY, quantity="0.5"),
        lots=lots,
        now=NOW,
        tax_data_status="COMPLETE",
    )
    assert exact_quantity.proposed_quantity == exact_quantity.inventory_quantity


def test_all_available_inventory_uses_the_exact_fifo_total() -> None:
    lots = [
        inventory_lot("0.123456789012345678901", "100", NOW - timedelta(days=2)),
        inventory_lot(
            "0.876543210987654321099",
            "200",
            NOW - timedelta(days=1),
            sequence=1,
        ),
    ]

    result = simulate_sale(
        simulation_id=uuid4(),
        request=proposal(SaleMode.ALL_AVAILABLE_INVENTORY),
        lots=lots,
        now=NOW,
        tax_data_status="COMPLETE",
    )

    assert result.proposed_quantity == Decimal("1.000000000000000000000")
    assert result.proposed_quantity == result.inventory_quantity
    assert (
        exact_decimal_sum(tuple(item.quantity for item in result.fifo_allocations))
        == result.proposed_quantity
    )


def test_one_year_boundary_leap_day_and_price_source() -> None:
    acquired = datetime(2024, 2, 29, 10, 30, tzinfo=UTC)
    before = datetime(2025, 2, 28, 10, 29, 59, tzinfo=UTC)
    at_boundary = datetime(2025, 2, 28, 10, 30, tzinfo=UTC)
    lot = inventory_lot("1", "1", acquired)
    before_result = simulate_sale(
        simulation_id=uuid4(),
        request=proposal(SaleMode.QUANTITY, quantity="1", price_at=before),
        lots=[lot],
        now=before,
        tax_data_status="COMPLETE",
    )
    boundary_result = simulate_sale(
        simulation_id=uuid4(),
        request=proposal(SaleMode.QUANTITY, quantity="1", price_at=at_boundary),
        lots=[lot],
        now=at_boundary,
        tax_data_status="COMPLETE",
    )
    assert before_result.fifo_allocations[0].reached_one_year is False
    assert boundary_result.fifo_allocations[0].reached_one_year is True
    assert boundary_result.fifo_allocations[0].holding_seconds == 365 * 86400
    fixed = FixedReferencePriceSource(
        price=proposal(SaleMode.ALL_AVAILABLE_INVENTORY).reference_price
    )
    assert fixed.get("ETH", now=NOW).source == "FAKE_TEST"


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: ReferencePrice(price_eur=Decimal("0"), source="x", timestamp=NOW),
            "greater",
        ),
        (
            lambda: ReferencePrice(price_eur=Decimal("1"), source="", timestamp=NOW),
            "empty",
        ),
        (lambda: inventory_lot("0", "1", NOW), "greater"),
        (lambda: inventory_lot("1", "0", NOW), "greater"),
        (lambda: inventory_lot("1", "1", NOW, sequence=-1), "sequence"),
        (lambda: proposal(SaleMode.QUANTITY), "quantity is required"),
        (lambda: proposal(SaleMode.TARGET_EUR), "target_eur is required"),
        (
            lambda: proposal(SaleMode.ALL_AVAILABLE_INVENTORY, quantity="1"),
            "only valid",
        ),
        (
            lambda: proposal(SaleMode.QUANTITY, quantity="1", target_eur="1"),
            "only valid",
        ),
        (lambda: proposal(SaleMode.QUANTITY, quantity="0"), "greater"),
        (lambda: proposal(SaleMode.TARGET_EUR, target_eur="-1"), "greater"),
        (lambda: proposal(SaleMode.QUANTITY, quantity="1", fee="-1"), "negative"),
    ],
)
def test_domain_validation(factory: object, message: str) -> None:
    callable_factory = factory
    assert callable(callable_factory)
    with pytest.raises((ValueError, TypeError), match=message):
        callable_factory()


def test_simulation_rejects_empty_excessive_future_lot_and_excessive_fee() -> None:
    valid = inventory_lot("1", "100", NOW - timedelta(days=1))
    cases = (
        ([], proposal(SaleMode.QUANTITY, quantity="1"), "UNKNOWN_OR_EMPTY_ASSET"),
        (
            [valid],
            proposal(SaleMode.QUANTITY, quantity="2"),
            "INSUFFICIENT_FIFO_INVENTORY",
        ),
        (
            [replace(valid, acquired_at=NOW + timedelta(seconds=1))],
            proposal(SaleMode.QUANTITY, quantity="1"),
            "ACQUISITION_AFTER_SIMULATED_SALE",
        ),
        (
            [valid],
            proposal(SaleMode.QUANTITY, quantity="1", price="1", fee="2"),
            "FEE_EXCEEDS_PROCEEDS",
        ),
    )
    for lots, request, code in cases:
        with pytest.raises(SaleProposalError) as error:
            simulate_sale(
                simulation_id=uuid4(),
                request=request,
                lots=lots,
                now=NOW,
                tax_data_status="COMPLETE",
            )
        assert error.value.code == code


def _acquisition(asset: str, quantity: str, occurred_at: datetime) -> AcquisitionLot:
    key = uuid4().hex
    return AcquisitionLot(
        stable_key=key,
        payload_hash=key * 2,
        asset_raw_code=asset,
        asset_code=asset,
        asset_mapping_version="test-v1",
        quantity=Decimal(quantity),
        occurred_at=occurred_at,
        acquisition_type=AcquisitionType.STAKING_REWARD,
        provider="synthetic",
        account_scope="test",
        wallet_scope="test-wallet",
        external_id=key,
        transformation_version="test-v1",
        valuation_status=ValuationStatus.VALUATION_REQUIRED,
        tax_treatment_hint=TaxTreatmentHint.PASSIVE_STAKING_REWARD,
    )


def _disposal(asset: str, quantity: str, occurred_at: datetime) -> DisposalEvent:
    key = uuid4().hex
    return DisposalEvent(
        stable_key=key,
        payload_hash=key * 2,
        asset_raw_code=asset,
        asset_code=asset,
        asset_mapping_version="test-v1",
        quantity=Decimal(quantity),
        occurred_at=occurred_at,
        disposal_type=DisposalType.TRADE_SELL,
        provider="synthetic",
        account_scope="test",
        wallet_scope="test-wallet",
        external_id=key,
        transformation_version="test-v1",
        valuation_status=ValuationStatus.VALUATION_REQUIRED,
        tax_treatment_hint=TaxTreatmentHint.TRADE_DISPOSAL,
    )


def _valuation(
    lot: AcquisitionLot | DisposalEvent,
    transformation: TransformationRun,
    valuation_run: ValuationRun,
    value: str,
    *,
    fee_review_required: bool = False,
) -> tuple[ValuationRequirement, ValuationDecision]:
    requirement = ValuationRequirement(
        asset_code=lot.asset_code,
        target_currency="EUR",
        valuation_at=lot.occurred_at,
        method=ValuationMethod.DAILY_AVERAGE,
        status=ValuationStatus.VALUATION_REQUIRED,
        reason_code="synthetic_sale_test",
        domain_object_type="AcquisitionLot",
        domain_object_id=lot.id,
        transformation_run_id=transformation.id,
    )
    decision = ValuationDecision(
        valuation_requirement_id=requirement.id,
        valuation_run_id=valuation_run.id,
        domain_object_type="AcquisitionLot",
        domain_object_id=lot.id,
        asset_code=lot.asset_code,
        quantity=lot.quantity,
        valuation_at=lot.occurred_at,
        price_date=lot.occurred_at.date(),
        method=PriceMethod.MANUAL_DAILY_PRICE,
        unit_price_eur=Decimal(value) / lot.quantity,
        eur_value=Decimal(value),
        price_source="synthetic",
        provider="manual",
        provider_object_id=None,
        provider_contract_version="manual-v1",
        method_version="eur-valuation-v2",
        sample_count=1,
        fetched_at=NOW,
        decided_at=NOW,
        status=ValuationDecisionStatus.RESOLVED,
        reason_code="valuation_resolved",
        net_quantity=lot.quantity,
        net_acquisition_value_eur=Decimal(value),
        valuation_basis="synthetic",
        fee_tax_classification=(
            FeeTaxClassification.WERBUNGSKOSTEN_CANDIDATE
            if fee_review_required
            else FeeTaxClassification.NOT_APPLICABLE
        ),
        fee_tax_review_status=(
            FeeTaxReviewStatus.REVIEW_REQUIRED
            if fee_review_required
            else FeeTaxReviewStatus.NOT_REQUIRED
        ),
    )
    return requirement, decision


def _seed(database: Session) -> None:
    transformation = TransformationRun(
        contract_version="test-v1",
        status=TransformationStatus.COMPLETED,
        started_at=NOW,
        completed_at=NOW,
        actor_id="test-suite",
    )
    valuation_run = ValuationRun(
        provider="manual",
        correlation_id=uuid4(),
        started_at=NOW,
        ended_at=NOW,
        status=ValuationRunStatus.COMPLETED,
    )
    old_run = TaxCalculationRun(
        period_start=NOW.date().replace(month=1, day=1),
        period_end=NOW.date().replace(month=12, day=31),
        snapshot_hash="a" * 64,
        rules_fingerprint="b" * 64,
        status=TaxRunStatus.COMPLETED,
        started_at=NOW - timedelta(days=1),
        ended_at=NOW - timedelta(days=1),
    )
    run = TaxCalculationRun(
        period_start=NOW.date().replace(month=1, day=1),
        period_end=NOW.date().replace(month=12, day=31),
        snapshot_hash="c" * 64,
        rules_fingerprint="d" * 64,
        status=TaxRunStatus.COMPLETED_WITH_REVIEW,
        started_at=NOW,
        ended_at=NOW,
    )
    database.add_all((transformation, valuation_run, old_run, run))
    values = (
        ("ETH", "2", "2000", NOW - timedelta(days=400), "1"),
        ("ETH", "1", "1500", NOW - timedelta(days=100), "1"),
        ("USDC", "1", "1", NOW - timedelta(days=10), "1"),
        ("ETHW", "1", "1", NOW - timedelta(days=9), "1"),
    )
    decisions: list[ValuationDecision] = []
    for sequence, (asset, original, value, occurred_at, remaining) in enumerate(values):
        lot = _acquisition(asset, original, occurred_at)
        requirement, decision = _valuation(lot, transformation, valuation_run, value)
        decisions.append(decision)
        database.add_all(
            (
                lot,
                requirement,
                decision,
                InventoryLot(
                    tax_calculation_run_id=run.id,
                    acquisition_lot_id=lot.id,
                    asset_code=asset,
                    original_quantity=Decimal(original),
                    remaining_quantity=Decimal(remaining),
                    acquired_at=occurred_at,
                    acquisition_value_eur=Decimal(value),
                    acquisition_fee_eur=Decimal("0"),
                    remaining_cost_eur=Decimal(value),
                    valuation_decision_id=decision.id,
                    rule_version="fifo-utc-stable-v1",
                    sequence=sequence,
                ),
            )
        )
        if asset == "USDC":
            database.add(
                replace(
                    decision,
                    id=uuid4(),
                    version=2,
                    supersedes_id=decision.id,
                )
            )
    xrp = _acquisition("XRP", "1", NOW - timedelta(days=5))
    database.add_all(
        (
            xrp,
            InventoryLot(
                tax_calculation_run_id=run.id,
                acquisition_lot_id=xrp.id,
                asset_code="XRP",
                original_quantity=Decimal("1"),
                remaining_quantity=Decimal("1"),
                acquired_at=xrp.occurred_at,
                acquisition_value_eur=Decimal("1"),
                acquisition_fee_eur=Decimal("0"),
                remaining_cost_eur=Decimal("1"),
                valuation_decision_id=uuid4(),
                rule_version="fifo-utc-stable-v1",
                sequence=4,
            ),
        )
    )
    database.flush()
    for _ in range(380):
        staking_lot = _acquisition("ETH", "1", NOW - timedelta(days=400))
        requirement, decision = _valuation(
            staking_lot,
            transformation,
            valuation_run,
            "1",
            fee_review_required=True,
        )
        database.add_all((staking_lot, requirement, decision))
    for _ in range(48):
        decided_lot = _acquisition("ETH", "1", NOW - timedelta(days=500))
        requirement, decision = _valuation(
            decided_lot,
            transformation,
            valuation_run,
            "1",
            fee_review_required=True,
        )
        review_case = TaxReviewCase(
            tax_calculation_run_id=run.id,
            code="tax_staking_platform_fee_candidate_review",
            message="synthetic decided staking fee review",
            source_object_type="ValuationDecision",
            source_object_id=decision.id,
            occurred_at=NOW,
        )
        review_decision = TaxReviewDecision(
            valuation_decision_id=decision.id,
            source_tax_review_case_id=review_case.id,
            decision=TaxReviewDecisionValue.EXCLUDE_FROM_WERBUNGSKOSTEN,
            reason="Synthetic effective decision.",
            actor_id="test-suite",
            decided_at=NOW,
            version=1,
            batch_id=uuid4(),
        )
        database.add_all(
            (decided_lot, requirement, decision, review_case, review_decision)
        )
    ada = _acquisition("ADA", "5", NOW - timedelta(hours=12))
    ada_requirement, ada_decision = _valuation(ada, transformation, valuation_run, "10")
    database.add_all((ada, ada_requirement, ada_decision))
    database.add_all(
        (
            FinancialReviewResolution(
                id=PENDING_RESOLUTION_ID,
                transformation_issue_id=uuid4(),
                resolution_type=FinancialReviewType.DELISTING_LIQUIDATION,
                status=ResolutionStatus.CONFIRMED,
                decided_at=NOW,
                decided_by="test-user",
                reason="synthetic",
                source="USER_CONFIRMED",
                confidence=ReviewConfidence.HIGH,
                tax_mapping_status=TaxMappingStatus.PENDING,
                metadata={
                    "disposed_asset": "ETHW",
                    "proceeds_asset": "USDC",
                    "proceeds_quantity": "0.60700172",
                },
            ),
            FinancialReviewResolution(
                transformation_issue_id=uuid4(),
                resolution_type=FinancialReviewType.OWN_ACCOUNT_FIAT_WITHDRAWAL,
                status=ResolutionStatus.CONFIRMED,
                decided_at=NOW,
                decided_by="test-user",
                reason="synthetic",
                source="USER_CONFIRMED",
                confidence=None,
                tax_mapping_status=TaxMappingStatus.NOT_REQUIRED,
                metadata={"fee_tax_status": "REVIEW_REQUIRED"},
            ),
        )
    )
    database.commit()


@pytest.fixture
def sale_api() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as database:
        _seed(database)

    def dependency() -> Iterator[Session]:
        with sessions() as database:
            yield database

    app.dependency_overrides[get_session] = dependency
    try:
        with TestClient(app) as client:
            yield client, sessions
    finally:
        app.dependency_overrides.clear()


def _counts(sessions: sessionmaker[Session]) -> tuple[int, ...]:
    with sessions() as database:
        return tuple(
            database.scalar(select(func.count()).select_from(model)) or 0
            for model in (
                TaxCalculationRun,
                InventoryLot,
                LotAllocation,
                DisposalCalculation,
                DisposalEvent,
                TaxJournalEntry,
                TaxReviewCase,
                TaxReviewDecision,
                FinancialReviewResolution,
            )
        )


def test_api_quantity_proposal_warnings_and_no_persistence(
    sale_api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, sessions = sale_api
    before = _counts(sessions)
    inventory = client.get("/api/sale-proposals/inventory")
    assert inventory.status_code == 200
    listing = inventory.json()
    assert listing["tax_data_status"] == "PARTIAL"
    assert "OPEN_STAKING_PLATFORM_FEE_REVIEWS:380" in listing["warnings"]
    assert "PENDING_FINANCIAL_TAX_MAPPINGS:1" in listing["warnings"]
    assert "OPEN_WITHDRAWAL_FEE_TAX_REVIEWS:1" in listing["warnings"]
    assert next(item for item in listing["items"] if item["asset"] == "USDC") == {
        "asset": "USDC",
        "inventory_quantity": "1",
        "exchange_available_quantity": None,
        "blocked": False,
        "blocked_reasons": [],
    }
    assert (
        next(item for item in listing["items"] if item["asset"] == "XRP")["blocked"]
        is True
    )
    ethw_listing = next(item for item in listing["items"] if item["asset"] == "ETHW")
    assert ethw_listing["blocked_reasons"] == ["UNRESOLVED_FINANCIAL_TAX_MAPPING"]

    response = client.post(
        "/api/sale-proposals/simulate",
        json={
            "asset": "eth",
            "mode": "quantity",
            "quantity": "1.5",
            "reference_price_eur": "2000.000000000000000001",
            "estimated_fee_eur": "1",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True
    assert body["order_created"] is False
    assert body["exchange_mutated"] is False
    assert body["tax_run_created"] is False
    assert body["price_source"] == "MANUAL_SIMULATION"
    assert body["execution_price_guaranteed"] is False
    assert body["exchange_available_quantity"] is None
    assert body["inventory_quantity"] == "431"
    assert body["available_inventory_quantity"] == "431"
    assert [item["quantity"] for item in body["fifo_allocations"]] == ["1", "0.5"]
    assert body["tax_data_status"] == "PARTIAL"
    assert body["tax_hint_version"] == "de-bmf-crypto-2025-03-06-v1"
    assert len(body["tax_hints"]) == 4
    assert "Steuerliche Einordnung" in body["tax_notice"]
    assert _counts(sessions) == before


def test_api_modes_stale_price_and_current_valuation_cost(
    sale_api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = sale_api
    target = client.post(
        "/api/sale-proposals/simulate",
        json={
            "asset": "ETH",
            "mode": "target_eur",
            "target_eur": "1000",
            "reference_price_eur": "2000",
            "price_timestamp": (NOW - timedelta(days=1)).isoformat(),
        },
    )
    assert target.status_code == 200
    assert target.json()["proposed_quantity"] == "0.5"
    assert "REFERENCE_PRICE_STALE" in target.json()["warnings"]
    all_inventory = client.post(
        "/api/sale-proposals/simulate",
        json={
            "asset": "ETH",
            "mode": "all_available_inventory",
            "reference_price_eur": "2000",
        },
    )
    assert all_inventory.status_code == 200
    assert all_inventory.json()["proposed_quantity"] == "431"
    assert all_inventory.json()["acquisition_cost_eur"] == "3928"
    unrelated_usdc = client.post(
        "/api/sale-proposals/simulate",
        json={
            "asset": "USDC",
            "mode": "quantity",
            "quantity": "0.1",
            "reference_price_eur": "1",
        },
    )
    assert unrelated_usdc.status_code == 200
    assert unrelated_usdc.json()["inventory_quantity"] == "1"


def test_inventory_and_simulation_include_lots_added_after_the_latest_tax_run(
    sale_api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, sessions = sale_api
    before = _counts(sessions)
    with sessions() as database:
        assert database.scalar(select(func.count()).select_from(TaxCalculationRun)) == 2
        assert (
            database.scalar(
                select(func.count())
                .select_from(InventoryLot)
                .where(InventoryLot.asset_code == "ADA")
            )
            == 0
        )
        assert database.scalar(select(func.count()).select_from(TaxReviewCase)) == 48
        assert (
            database.scalar(select(func.count()).select_from(TaxReviewDecision)) == 48
        )

    inventory = client.get("/api/sale-proposals/inventory")

    assert inventory.status_code == 200
    ada = next(item for item in inventory.json()["items"] if item["asset"] == "ADA")
    assert ada["inventory_quantity"] == "5"
    assert ada["blocked"] is False
    assert "OPEN_STAKING_PLATFORM_FEE_REVIEWS:380" in inventory.json()["warnings"]

    simulation = client.post(
        "/api/sale-proposals/simulate",
        json={
            "asset": "ADA",
            "mode": "quantity",
            "quantity": "2",
            "reference_price_eur": "3",
        },
    )

    assert simulation.status_code == 200
    assert simulation.json()["proposed_quantity"] == "2"
    assert simulation.json()["inventory_quantity"] == "5"
    assert simulation.json()["order_created"] is False
    assert simulation.json()["tax_run_created"] is False
    assert _counts(sessions) == before


def test_current_inventory_applies_existing_disposals_with_tax_fifo(
    sale_api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, sessions = sale_api
    with sessions() as database:
        transformation = database.scalar(select(TransformationRun))
        valuation_run = database.scalar(select(ValuationRun))
        assert transformation is not None
        assert valuation_run is not None
        acquisition = _acquisition("BTC", "10", NOW - timedelta(days=20))
        disposal = _disposal("BTC", "3", NOW - timedelta(days=10))
        acquisition_requirement, acquisition_decision = _valuation(
            acquisition, transformation, valuation_run, "100"
        )
        disposal_requirement, disposal_decision = _valuation(
            disposal, transformation, valuation_run, "60"
        )
        database.add_all(
            (
                acquisition,
                disposal,
                acquisition_requirement,
                acquisition_decision,
                disposal_requirement,
                disposal_decision,
            )
        )
        database.commit()
    before = _counts(sessions)

    inventory = client.get("/api/sale-proposals/inventory")

    assert inventory.status_code == 200
    btc = next(item for item in inventory.json()["items"] if item["asset"] == "BTC")
    assert btc["inventory_quantity"] == "7"
    simulation = client.post(
        "/api/sale-proposals/simulate",
        json={
            "asset": "BTC",
            "mode": "all_available_inventory",
            "reference_price_eur": "20",
        },
    )
    assert simulation.status_code == 200
    assert simulation.json()["proposed_quantity"] == "7"
    assert simulation.json()["acquisition_cost_eur"] == "70"
    assert _counts(sessions) == before


def test_empty_test_database_has_complete_read_only_inventory() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        result = sale_inventory(database)
    assert result == {
        "items": [],
        "tax_data_status": "COMPLETE",
        "warnings": ["EXCHANGE_BALANCE_NOT_RECONCILED"],
    }


@pytest.mark.parametrize(
    ("payload", "status", "code"),
    [
        (
            {
                "asset": "EUR",
                "mode": "quantity",
                "quantity": "1",
                "reference_price_eur": "1",
            },
            422,
            "EUR_CRYPTO_SALE_NOT_ALLOWED",
        ),
        (
            {
                "asset": "DOGE",
                "mode": "quantity",
                "quantity": "1",
                "reference_price_eur": "1",
            },
            404,
            "UNKNOWN_ASSET",
        ),
        (
            {
                "asset": "XRP",
                "mode": "quantity",
                "quantity": "1",
                "reference_price_eur": "1",
            },
            409,
            "INCOMPLETE_ASSET_VALUATION",
        ),
        (
            {
                "asset": "ETHW",
                "mode": "quantity",
                "quantity": "1",
                "reference_price_eur": "1",
            },
            409,
            "UNRESOLVED_FINANCIAL_TAX_MAPPING",
        ),
        (
            {
                "asset": "ETH",
                "mode": "quantity",
                "quantity": "432",
                "reference_price_eur": "1",
            },
            409,
            "INSUFFICIENT_FIFO_INVENTORY",
        ),
        ({"asset": "ETH", "mode": "quantity", "quantity": "1"}, 422, "missing"),
        (
            {
                "asset": "ETH",
                "mode": "quantity",
                "quantity": "0",
                "reference_price_eur": "1",
            },
            422,
            "value_error",
        ),
        (
            {
                "asset": " ",
                "mode": "quantity",
                "quantity": "1",
                "reference_price_eur": "1",
            },
            422,
            "value_error",
        ),
        (
            {
                "asset": "ETH",
                "mode": "quantity",
                "quantity": "1",
                "reference_price_eur": "1",
                "estimated_fee_eur": "-1",
            },
            422,
            "value_error",
        ),
        (
            {
                "asset": "ETH",
                "mode": "all_available_inventory",
                "quantity": "1",
                "reference_price_eur": "1",
            },
            422,
            "INVALID_SALE_PROPOSAL",
        ),
        (
            {
                "asset": "ETH",
                "mode": "quantity",
                "quantity": "1",
                "reference_price_eur": "1",
                "estimated_fee_eur": "2",
            },
            422,
            "FEE_EXCEEDS_PROCEEDS",
        ),
        (
            {
                "asset": "ETH",
                "mode": "quantity",
                "quantity": "-1",
                "reference_price_eur": "1",
            },
            422,
            "value_error",
        ),
        (
            {
                "asset": "ETH",
                "mode": "quantity",
                "quantity": "1",
                "reference_price_eur": "malformed",
            },
            422,
            "decimal_parsing",
        ),
        (
            {
                "asset": "ETH",
                "mode": "quantity",
                "quantity": "1",
                "reference_price_eur": "NaN",
            },
            422,
            "finite_number",
        ),
        (
            {
                "asset": "ETH",
                "mode": "quantity",
                "quantity": "1",
                "reference_price_eur": "0",
            },
            422,
            "value_error",
        ),
    ],
)
def test_api_rejects_unsafe_or_invalid_requests(
    sale_api: tuple[TestClient, sessionmaker[Session]],
    payload: dict[str, str],
    status: int,
    code: str,
) -> None:
    response = sale_api[0].post("/api/sale-proposals/simulate", json=payload)
    assert response.status_code == status
    detail = response.json()["detail"]
    if isinstance(detail, dict) and detail["code"] != "request_validation_failed":
        assert detail["code"] == code
    else:
        errors = detail["errors"] if isinstance(detail, dict) else detail
        assert any(item["type"] == code for item in errors)


def test_source_has_no_exchange_write_client_or_order_route() -> None:
    root = Path(__file__).parents[1]
    sources = "\n".join(
        (root / relative).read_text(encoding="utf-8").lower()
        for relative in ("app/core/sale_planner.py", "app/api/sale_proposals.py")
    )
    for forbidden in (
        "addorder",
        "cancelorder",
        "editorder",
        '"/0/private/withdraw"',
        '"/0/private/deposit',
        '"/0/private/transfer',
        "taxcalculationrun(",
        "db.add(",
        "db.commit(",
    ):
        assert forbidden not in sources


class _BalanceClient:
    def __init__(
        self,
        snapshot: ExchangeBalanceSnapshot | None = None,
        error: KrakenPrivateError | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.error = error

    def extended_balance(self) -> ExchangeBalanceSnapshot:
        if self.error is not None:
            raise self.error
        assert self.snapshot is not None
        return self.snapshot


class _MarketClient:
    def __init__(
        self,
        quote: KrakenMarketQuote | None = None,
        error: KrakenMarketError | None = None,
    ) -> None:
        self.quote = quote
        self.error = error

    def eur_quote(self, asset: str) -> KrakenMarketQuote:
        if self.error is not None:
            raise self.error
        assert self.quote is not None
        assert self.quote.asset == asset
        return self.quote


def _extended_balance(
    *,
    provider_code: str,
    asset: str | None,
    balance: str,
    available: str,
    extension: str | None = None,
) -> KrakenExtendedBalance:
    return KrakenExtendedBalance(
        provider_asset_code=provider_code,
        canonical_asset=asset,
        balance=Decimal(balance),
        credit=Decimal("0"),
        credit_used=Decimal("0"),
        hold_trade=Decimal(balance) - Decimal(available),
        calculated_available=Decimal(available),
        extension=extension,
        balance_kind="non_spot" if extension else "spot",
        provider_metadata={},
    )


def _snapshot(
    *, spot_balance: str = "430", non_spot_balance: str = "1"
) -> ExchangeBalanceSnapshot:
    return ExchangeBalanceSnapshot(
        fetched_at=NOW,
        balances=(
            _extended_balance(
                provider_code="XETH",
                asset="ETH",
                balance=spot_balance,
                available=spot_balance,
            ),
            _extended_balance(
                provider_code="ETH.B",
                asset="ETH",
                balance=non_spot_balance,
                available=non_spot_balance,
                extension="B",
            ),
            _extended_balance(
                provider_code="?",
                asset=None,
                balance="2",
                available="2",
            ),
        ),
        warnings=("KRAKEN_NON_SPOT_BALANCE_PRESENT", "KRAKEN_UNKNOWN_ASSET_CODE"),
    )


def _quote(*, asset: str = "ETH", bid: str = "3") -> KrakenMarketQuote:
    return KrakenMarketQuote(
        asset=asset,
        quote_asset="EUR",
        pair=f"{asset}EUR",
        best_bid_eur=Decimal(bid),
        best_ask_eur=Decimal("3.1"),
        last_trade_eur=Decimal("2.9"),
        selected_reference_price_eur=Decimal(bid),
        selected_reference="KRAKEN_BEST_BID",
        fetched_at=NOW,
    )


def _install_live_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    balance: _BalanceClient | None = None,
    market: _MarketClient | None = None,
) -> None:
    monkeypatch.setattr(
        sale_proposals_api,
        "build_balance_client",
        lambda settings: balance or _BalanceClient(_snapshot()),
    )
    monkeypatch.setattr(
        sale_proposals_api,
        "build_market_client",
        lambda settings: market or _MarketClient(_quote()),
    )


def test_live_context_reconciles_spot_non_spot_and_preserves_tax_warnings(
    sale_api: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, sessions = sale_api
    before = _counts(sessions)
    _install_live_fakes(monkeypatch)

    response = client.get("/api/sale-proposals/live-context?asset=eth")

    assert response.status_code == 200
    body = response.json()
    assert body["asset"] == "ETH"
    assert body["inventory_quantity"] == "431"
    assert body["exchange_total_balance"] == "431"
    assert body["exchange_spot_available_quantity"] == "430"
    assert body["exchange_non_spot_balance"] == "1"
    assert body["inventory_exchange_difference"] == "0"
    assert body["reconciliation_status"] == "MATCH"
    assert body["safe_simulatable_quantity"] == "430"
    assert body["reference_price_eur"] == "3"
    assert body["best_ask_eur"] == "3.1"
    assert body["last_trade_eur"] == "2.9"
    assert body["price_source"] == "KRAKEN_BEST_BID"
    assert body["execution_price_guaranteed"] is False
    assert body["balance_permission_available"] is True
    assert len(body["balance_details"]) == 3
    assert "KRAKEN_NON_SPOT_BALANCE_PRESENT" in body["warnings"]
    assert "KRAKEN_UNKNOWN_ASSET_CODE" in body["warnings"]
    assert "OPEN_STAKING_PLATFORM_FEE_REVIEWS:380" in body["warnings"]
    assert "PENDING_FINANCIAL_TAX_MAPPINGS:1" in body["warnings"]
    assert "OPEN_WITHDRAWAL_FEE_TAX_REVIEWS:1" in body["warnings"]
    assert _counts(sessions) == before


@pytest.mark.parametrize(
    ("spot", "non_spot", "difference"),
    [("429", "1", "1"), ("431", "1", "-1")],
)
def test_live_context_reports_both_inventory_difference_directions(
    sale_api: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
    spot: str,
    non_spot: str,
    difference: str,
) -> None:
    _install_live_fakes(
        monkeypatch,
        balance=_BalanceClient(_snapshot(spot_balance=spot, non_spot_balance=non_spot)),
    )
    response = sale_api[0].get("/api/sale-proposals/live-context?asset=ETH")
    assert response.status_code == 200
    assert response.json()["difference_quantity"] == difference
    assert response.json()["reconciliation_status"] == "DIFFERENCE"
    assert "EXCHANGE_INVENTORY_DIFFERENCE" in response.json()["warnings"]


@pytest.mark.parametrize(
    ("code", "warning", "permission"),
    [
        (
            "kraken_not_configured",
            "KRAKEN_API_KEY_NOT_CONFIGURED",
            False,
        ),
        (
            "kraken_balance_permission_missing",
            "KRAKEN_BALANCE_PERMISSION_MISSING",
            False,
        ),
        ("kraken_authentication_failed", "KRAKEN_AUTHENTICATION_FAILED", False),
        ("kraken_rate_limited", "KRAKEN_RATE_LIMITED", False),
        ("kraken_invalid_response", "KRAKEN_BALANCE_INVALID_RESPONSE", False),
        ("kraken_unavailable", "EXCHANGE_BALANCE_UNAVAILABLE", False),
    ],
)
def test_live_context_keeps_market_quote_when_balance_is_unavailable(
    sale_api: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    warning: str,
    permission: bool,
) -> None:
    _install_live_fakes(
        monkeypatch,
        balance=_BalanceClient(error=KrakenPrivateError(code, "expected")),
    )
    response = sale_api[0].get("/api/sale-proposals/live-context?asset=ETH")
    assert response.status_code == 200
    body = response.json()
    assert body["exchange_balance_available"] is False
    assert body["balance_permission_available"] is permission
    assert body["reconciliation_status"] == "UNKNOWN"
    assert body["difference_quantity"] is None
    assert body["price_available"] is True
    assert warning in body["warnings"]
    assert "EXCHANGE_BALANCE_UNAVAILABLE" in body["warnings"]


def test_live_context_reports_unavailable_eur_pair_and_unknown_asset(
    sale_api: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_live_fakes(
        monkeypatch,
        market=_MarketClient(
            error=KrakenMarketError("kraken_eur_pair_unavailable", "missing")
        ),
    )
    response = sale_api[0].get("/api/sale-proposals/live-context?asset=ETH")
    assert response.status_code == 200
    assert response.json()["price_available"] is False
    assert "KRAKEN_EUR_PAIR_UNAVAILABLE" in response.json()["warnings"]
    unknown = sale_api[0].get("/api/sale-proposals/live-context?asset=ZZZ")
    assert unknown.status_code == 404
    invalid = sale_api[0].get("/api/sale-proposals/live-context?asset=ETH.B")
    assert invalid.status_code == 422


def test_live_context_blocks_incomplete_asset_and_client_factories_are_read_only(
    sale_api: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    balance_client = sale_proposals_api.build_balance_client(
        Settings(kraken_api_key="query-key", kraken_api_secret="c2VjcmV0")
    )
    market_client = sale_proposals_api.build_market_client(Settings())
    assert balance_client.balance_path == "/0/private/BalanceEx"
    assert market_client.asset_pairs_path == "/0/public/AssetPairs"

    xrp_balance = ExchangeBalanceSnapshot(
        fetched_at=NOW,
        balances=(
            _extended_balance(
                provider_code="XRP", asset="XRP", balance="1", available="1"
            ),
        ),
        warnings=(),
    )
    _install_live_fakes(
        monkeypatch,
        balance=_BalanceClient(xrp_balance),
        market=_MarketClient(_quote(asset="XRP")),
    )
    response = sale_api[0].get("/api/sale-proposals/live-context?asset=XRP")
    assert response.status_code == 200
    assert response.json()["blocked_reasons"] == ["INCOMPLETE_ASSET_VALUATION"]


@pytest.mark.parametrize(
    ("payload", "expected_quantity"),
    [
        ({"asset": "ETH", "mode": "quantity", "quantity": "2"}, "2"),
        ({"asset": "ETH", "mode": "target_eur", "target_eur": "6"}, "2"),
        ({"asset": "ETH", "mode": "all_available_inventory"}, "3"),
    ],
)
def test_live_simulation_modes_use_bid_and_safe_exchange_max_without_persistence(
    sale_api: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, str],
    expected_quantity: str,
) -> None:
    client, sessions = sale_api
    before = _counts(sessions)
    _install_live_fakes(
        monkeypatch,
        balance=_BalanceClient(_snapshot(spot_balance="3", non_spot_balance="1")),
    )
    response = client.post("/api/sale-proposals/simulate-live", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["proposed_quantity"] == expected_quantity
    assert body["reference_price_eur"] == "3"
    assert body["price_source"] == "KRAKEN_BEST_BID"
    assert body["exchange_available_quantity"] == "3"
    assert body["safe_simulatable_quantity"] == "3"
    assert body["dry_run"] is True
    assert body["order_created"] is False
    assert body["exchange_mutated"] is False
    assert body["tax_run_created"] is False
    assert body["execution_price_guaranteed"] is False
    assert "EXCHANGE_BALANCE_NOT_RECONCILED" not in body["warnings"]
    assert "OPEN_STAKING_PLATFORM_FEE_REVIEWS:380" in body["warnings"]
    assert _counts(sessions) == before


@pytest.mark.parametrize(
    ("balance", "payload", "status", "code"),
    [
        (
            _BalanceClient(_snapshot(spot_balance="1", non_spot_balance="0")),
            {"asset": "ETH", "mode": "quantity", "quantity": "2"},
            409,
            "INSUFFICIENT_EXCHANGE_AVAILABLE_BALANCE",
        ),
        (
            _BalanceClient(_snapshot(spot_balance="500", non_spot_balance="0")),
            {"asset": "ETH", "mode": "quantity", "quantity": "432"},
            409,
            "INSUFFICIENT_FIFO_INVENTORY",
        ),
        (
            _BalanceClient(error=KrakenPrivateError("kraken_unavailable", "offline")),
            {"asset": "ETH", "mode": "quantity", "quantity": "1"},
            503,
            "EXCHANGE_BALANCE_UNAVAILABLE",
        ),
    ],
)
def test_live_simulation_blocks_unsafe_quantities_and_missing_balance(
    sale_api: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
    balance: _BalanceClient,
    payload: dict[str, str],
    status: int,
    code: str,
) -> None:
    _install_live_fakes(monkeypatch, balance=balance)
    response = sale_api[0].post("/api/sale-proposals/simulate-live", json=payload)
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code


def test_live_simulation_blocks_missing_price_and_financial_mapping(
    sale_api: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_live_fakes(
        monkeypatch,
        market=_MarketClient(error=KrakenMarketError("kraken_unavailable", "offline")),
    )
    no_price = sale_api[0].post(
        "/api/sale-proposals/simulate-live",
        json={"asset": "ETH", "mode": "quantity", "quantity": "1"},
    )
    assert no_price.status_code == 503
    assert no_price.json()["detail"]["code"] == "KRAKEN_PRICE_UNAVAILABLE"

    _install_live_fakes(
        monkeypatch,
        balance=_BalanceClient(
            ExchangeBalanceSnapshot(
                fetched_at=NOW,
                balances=(
                    _extended_balance(
                        provider_code="ETHW",
                        asset="ETHW",
                        balance="1",
                        available="1",
                    ),
                ),
                warnings=(),
            )
        ),
        market=_MarketClient(_quote(asset="ETHW")),
    )
    ethw = sale_api[0].post(
        "/api/sale-proposals/simulate-live",
        json={"asset": "ETHW", "mode": "quantity", "quantity": "1"},
    )
    assert ethw.status_code == 409
    assert ethw.json()["detail"]["code"] == "UNRESOLVED_FINANCIAL_TAX_MAPPING"


def test_live_simulation_request_shape_and_zero_exchange_are_conservative(
    sale_api: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_live_fakes(
        monkeypatch,
        balance=_BalanceClient(_snapshot(spot_balance="0", non_spot_balance="1")),
    )
    zero = sale_api[0].post(
        "/api/sale-proposals/simulate-live",
        json={"asset": "ETH", "mode": "all_available_inventory"},
    )
    assert zero.status_code == 409
    assert zero.json()["detail"]["code"] == "INSUFFICIENT_EXCHANGE_AVAILABLE_BALANCE"
    invalid = sale_api[0].post(
        "/api/sale-proposals/simulate-live",
        json={"asset": "", "mode": "quantity", "quantity": "1"},
    )
    assert invalid.status_code == 422


def test_core_live_simulation_rejects_negative_exchange_quantity() -> None:
    with pytest.raises(SaleProposalError) as raised:
        simulate_sale(
            simulation_id=uuid4(),
            request=proposal(SaleMode.QUANTITY, quantity="1"),
            lots=[inventory_lot("2", "100", NOW - timedelta(days=2))],
            now=NOW,
            tax_data_status="COMPLETE",
            exchange_available_quantity=Decimal("-1"),
            exchange_reconciled=True,
        )
    assert raised.value.code == "EXCHANGE_BALANCE_UNAVAILABLE"
