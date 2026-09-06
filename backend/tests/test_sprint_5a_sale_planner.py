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

from app.api.sale_proposals import sale_inventory
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
    TaxRunStatus,
)
from app.core.transformation import (
    AcquisitionLot,
    AcquisitionType,
    DisposalEvent,
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


def _valuation(
    lot: AcquisitionLot,
    transformation: TransformationRun,
    valuation_run: ValuationRun,
    value: str,
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
        fee_tax_classification=FeeTaxClassification.WERBUNGSKOSTEN_CANDIDATE,
        fee_tax_review_status=FeeTaxReviewStatus.REVIEW_REQUIRED,
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
    staking_fee_decisions = [decisions[0]]
    for _ in range(379):
        staking_lot = _acquisition("ETH", "1", NOW - timedelta(days=400))
        requirement, decision = _valuation(
            staking_lot, transformation, valuation_run, "1"
        )
        database.add_all((staking_lot, requirement, decision))
        staking_fee_decisions.append(decision)
    database.add_all(
        TaxReviewCase(
            tax_calculation_run_id=run.id,
            code="tax_staking_platform_fee_candidate_review",
            message="synthetic open staking fee review",
            source_object_type="ValuationDecision",
            source_object_id=decision.id,
            occurred_at=NOW,
        )
        for decision in staking_fee_decisions
    )
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
    assert body["inventory_quantity"] == "2"
    assert body["available_inventory_quantity"] == "2"
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
    assert all_inventory.json()["proposed_quantity"] == "2"
    assert all_inventory.json()["acquisition_cost_eur"] == "2500"
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
                "quantity": "3",
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
        "kraken_private",
        "taxcalculationrun(",
        "db.add(",
        "db.commit(",
    ):
        assert forbidden not in sources
