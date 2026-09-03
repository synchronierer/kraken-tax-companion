import io
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Context, Decimal, getcontext, localcontext
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from pypdf import PdfReader
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.tax import _dict_list
from app.config.settings import get_settings
from app.core.entities import AuditEvent
from app.core.tax import (
    EXPORT_FORMAT_VERSIONS,
    AcquisitionInput,
    DisposalCalculation,
    DisposalInput,
    ExportArtifact,
    ExportKind,
    ExportRun,
    ExportStatus,
    InventoryLot,
    JournalEntryType,
    LotAllocation,
    TaxCalculationRun,
    TaxJournalEntry,
    TaxRecordStatus,
    TaxReportingPeriod,
    TaxReviewCase,
    TaxReviewDecision,
    TaxReviewDecisionValue,
    TaxRuleVersion,
    TaxRunStatus,
    calculate_fifo,
    effective_tax_review_decisions,
    tax_snapshot_hash,
)
from app.core.transformation import (
    AcquisitionLot,
    AcquisitionType,
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
    exact_decimal_subtract,
    exact_decimal_sum,
)
from app.database.base import Base
from app.database.session import get_session
from app.main import app
from app.services.tax_exports import (
    PDF_REPORT_VERSION,
    ReportAssetRow,
    ReportInventoryRow,
    ReportReviewEvidence,
    TaxReportData,
    csv_bytes,
    export_extension,
    safe_export_path,
    tax_report_pdf,
)

NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)


def assert_decimal_context_unchanged(before: Context) -> None:
    current = getcontext()
    assert current.prec == before.prec
    assert current.rounding == before.rounding
    assert current.Emin == before.Emin
    assert current.Emax == before.Emax
    assert current.capitals == before.capitals
    assert current.clamp == before.clamp
    assert dict(current.flags) == dict(before.flags)
    assert dict(current.traps) == dict(before.traps)


def acquisition(
    quantity: str,
    value: str,
    acquired_at: datetime,
    *,
    fee: str = "0",
    kind: str = "trade_buy",
) -> AcquisitionInput:
    return AcquisitionInput(
        acquisition_id=uuid4(),
        asset_code="btc",
        quantity=Decimal(quantity),
        acquired_at=acquired_at,
        value_eur=Decimal(value),
        fee_eur=Decimal(fee),
        valuation_decision_id=uuid4(),
        acquisition_type=kind,
        gross_income_eur=(Decimal(value) if "staking" in kind else None),
    )


def disposal(
    quantity: str,
    proceeds: str,
    disposed_at: datetime,
    *,
    fee: str = "0",
    kind: str = "trade_sell",
) -> DisposalInput:
    return DisposalInput(
        disposal_id=uuid4(),
        asset_code="BTC",
        quantity=Decimal(quantity),
        disposed_at=disposed_at,
        proceeds_eur=Decimal(proceeds),
        fee_eur=Decimal(fee),
        valuation_decision_id=uuid4(),
        disposal_type=kind,
    )


def test_fifo_partial_multiple_lots_fees_and_stable_tie_break() -> None:
    run_id = uuid4()
    rules = TaxRuleVersion()
    first = acquisition("1", "100", NOW - timedelta(days=20), fee="2")
    second = acquisition("2", "300", NOW - timedelta(days=10), fee="3")
    third = acquisition("1", "200", NOW - timedelta(days=5))
    sold = disposal("2", "500", NOW, fee="5")
    context_before = getcontext().copy()

    result = calculate_fifo(
        run_id=run_id,
        period=TaxReportingPeriod.for_year(2026),
        rules=rules,
        acquisitions=[third, second, first],
        disposals=[sold],
    )

    assert_decimal_context_unchanged(context_before)
    assert [item.acquisition_lot_id for item in result.lots] == [
        first.acquisition_id,
        second.acquisition_id,
        third.acquisition_id,
    ]
    assert [item.allocated_quantity for item in result.allocations] == [
        Decimal("1"),
        Decimal("1"),
    ]
    assert sum(
        (item.disposal_proceeds_eur for item in result.allocations), Decimal("0")
    ) == Decimal("500")
    assert sum(
        (item.disposal_fee_eur for item in result.allocations), Decimal("0")
    ) == Decimal("5")
    assert result.lots[0].remaining_quantity == 0
    assert result.lots[1].remaining_quantity == 1
    assert result.lots[1].remaining_cost_eur == Decimal("151.5")
    assert result.lots[2].remaining_quantity == 1
    assert result.calculations[0].gain_loss_eur == Decimal("241.5")
    assert {item.entry_type for item in result.journal} >= {
        JournalEntryType.ACQUISITION,
        JournalEntryType.DISPOSAL,
        JournalEntryType.FEE,
        JournalEntryType.REALIZED_GAIN,
    }
    assert not result.reviews
    assert tax_snapshot_hash([first, second], [sold]) == tax_snapshot_hash(
        [second, first], [sold]
    )
    reward = acquisition("1", "90", NOW, kind="staking_reward")
    changed_reward_value = replace(reward, gross_income_eur=Decimal("101"))
    assert tax_snapshot_hash([changed_reward_value], [sold]) != (
        tax_snapshot_hash([reward], [sold])
    )


def test_fifo_review_loss_earn_exchange_and_validation() -> None:
    earned = acquisition(
        "1",
        "100",
        NOW - timedelta(days=2),
        kind="staking_reward",
    )
    exchanged = disposal(
        "2",
        "80",
        NOW,
        fee="1",
        kind="crypto_exchange",
    )
    result = calculate_fifo(
        run_id=uuid4(),
        period=TaxReportingPeriod.for_year(2026),
        rules=TaxRuleVersion(),
        acquisitions=[earned],
        disposals=[exchanged],
    )
    assert result.calculations[0].status is TaxRecordStatus.REVIEW_REQUIRED
    assert result.reviews[0].code == "tax_insufficient_inventory"
    assert any(
        item.entry_type is JournalEntryType.EARN_INFLOW for item in result.journal
    )
    assert any(item.entry_type is JournalEntryType.EXCHANGE for item in result.journal)
    assert any(
        item.entry_type is JournalEntryType.REALIZED_LOSS for item in result.journal
    )
    with pytest.raises(ValueError, match="start"):
        TaxReportingPeriod(start=date(2026, 2, 1), end=date(2026, 1, 1))
    with pytest.raises(ValueError, match="supported"):
        TaxReportingPeriod.for_year(1969)
    with pytest.raises((TypeError, ValueError)):
        acquisition("0", "1", NOW)
    with pytest.raises((TypeError, ValueError)):
        AcquisitionInput(
            acquisition_id=uuid4(),
            asset_code="BTC",
            quantity=Decimal("1"),
            acquired_at=NOW,
            value_eur=Decimal("1"),
            fee_eur=Decimal("0"),
            valuation_decision_id=uuid4(),
            acquisition_type="staking_reward",
            gross_income_eur=Decimal("1"),
            platform_fee_candidate_eur=Decimal("-1"),
        )


def test_staking_journal_never_treats_legacy_net_value_as_gross_income() -> None:
    legacy = acquisition("0.9", "90", NOW, kind="staking_reward")
    legacy = replace(legacy, gross_income_eur=None)
    result = calculate_fifo(
        run_id=uuid4(),
        period=TaxReportingPeriod.for_year(2026),
        rules=TaxRuleVersion(),
        acquisitions=[legacy],
        disposals=[],
    )
    entry = result.journal[0]
    assert entry.entry_type is JournalEntryType.REVIEW
    assert entry.status is TaxRecordStatus.REVIEW_REQUIRED
    assert entry.eur_value == Decimal("0")
    assert result.lots[0].original_quantity == Decimal("0.9")
    assert result.lots[0].acquisition_value_eur == Decimal("90")
    with pytest.raises((TypeError, ValueError)):
        disposal("1", "0", NOW)


def test_tax_acquisition_arithmetic_preserves_long_reward_values_and_context() -> None:
    gross = Decimal("51.586220962002859504121206557501435344829")
    fee = Decimal("11.964719979423988667047664309258439466617")
    net = Decimal("39.621500982578870837073542248242995878212")
    context_before = getcontext().copy()

    with localcontext() as unsafe_context:
        unsafe_context.prec = getcontext().prec
        assert net + fee != gross  # reproduces the unsafe default-context operation
    assert_decimal_context_unchanged(context_before)
    assert exact_decimal_sum((net, fee)) == gross
    item = AcquisitionInput(
        acquisition_id=uuid4(),
        asset_code="KAVA",
        quantity=Decimal("1.06809809"),
        acquired_at=NOW,
        value_eur=net,
        fee_eur=Decimal("0"),
        valuation_decision_id=uuid4(),
        acquisition_type="staking_reward",
        gross_income_eur=gross,
        platform_fee_candidate_eur=fee,
    )
    result = calculate_fifo(
        run_id=uuid4(),
        period=TaxReportingPeriod.for_year(2026),
        rules=TaxRuleVersion(),
        acquisitions=[item],
        disposals=[],
    )

    assert result.lots[0].acquisition_value_eur == net
    assert result.lots[0].remaining_cost_eur == net
    assert result.journal[0].eur_value == gross
    assert result.journal[0].acquisition_cost_eur == net
    assert_decimal_context_unchanged(context_before)


def test_tax_records_validate_immutability_inputs() -> None:
    digest = "a" * 64
    run = TaxCalculationRun(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        snapshot_hash=digest.upper(),
        rules_fingerprint=digest,
        status=TaxRunStatus.CREATED,
        started_at=NOW,
        ended_at=NOW + timedelta(minutes=1),
    )
    assert run.snapshot_hash == digest
    assert run.ended_at == NOW + timedelta(minutes=1)
    with pytest.raises(ValueError, match="SHA-256"):
        TaxCalculationRun(
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            snapshot_hash="bad",
            rules_fingerprint=digest,
            status=TaxRunStatus.CREATED,
            started_at=NOW,
        )
    with pytest.raises(ValueError, match="remaining_quantity"):
        InventoryLot(
            tax_calculation_run_id=uuid4(),
            acquisition_lot_id=uuid4(),
            asset_code="BTC",
            original_quantity=Decimal("1"),
            remaining_quantity=Decimal("2"),
            acquired_at=NOW,
            acquisition_value_eur=Decimal("1"),
            acquisition_fee_eur=Decimal("0"),
            remaining_cost_eur=Decimal("1"),
            valuation_decision_id=uuid4(),
            rule_version="v1",
            sequence=0,
        )
    with pytest.raises(ValueError, match="must not be negative"):
        TaxCalculationRun(
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            snapshot_hash=digest,
            rules_fingerprint=digest,
            status=TaxRunStatus.CREATED,
            started_at=NOW,
            checked_events=-1,
        )
    with pytest.raises(ValueError, match="sequence"):
        InventoryLot(
            tax_calculation_run_id=uuid4(),
            acquisition_lot_id=uuid4(),
            asset_code="BTC",
            original_quantity=Decimal("1"),
            remaining_quantity=Decimal("1"),
            acquired_at=NOW,
            acquisition_value_eur=Decimal("10"),
            acquisition_fee_eur=Decimal("0"),
            remaining_cost_eur=Decimal("10"),
            valuation_decision_id=uuid4(),
            rule_version="v1",
            sequence=-1,
        )
    with pytest.raises(ValueError, match="Allocation order"):
        LotAllocation(
            tax_calculation_run_id=uuid4(),
            disposal_event_id=uuid4(),
            inventory_lot_id=uuid4(),
            allocated_quantity=Decimal("1"),
            allocation_order=0,
            acquisition_cost_eur=Decimal("10"),
            disposal_proceeds_eur=Decimal("20"),
            disposal_fee_eur=Decimal("1"),
            gain_loss_eur=Decimal("9"),
            acquired_at=NOW - timedelta(days=1),
            disposed_at=NOW,
            holding_seconds=86400,
            fifo_rule_version="fifo-v1",
            fee_rule_version="fee-v1",
        )
    with pytest.raises(ValueError, match="allocated_quantity"):
        DisposalCalculation(
            tax_calculation_run_id=uuid4(),
            disposal_event_id=uuid4(),
            quantity=Decimal("1"),
            allocated_quantity=Decimal("2"),
            proceeds_eur=Decimal("20"),
            acquisition_cost_eur=Decimal("10"),
            fees_eur=Decimal("1"),
            gain_loss_eur=Decimal("9"),
            status=TaxRecordStatus.RESOLVED,
            rule_version="classification-v1",
        )
    with pytest.raises(ValueError, match="holding_seconds"):
        TaxJournalEntry(
            tax_calculation_run_id=uuid4(),
            occurred_at=NOW,
            tax_year=2026,
            entry_type=JournalEntryType.DISPOSAL,
            asset_code="BTC",
            quantity=Decimal("1"),
            eur_value=Decimal("20"),
            proceeds_eur=Decimal("20"),
            acquisition_cost_eur=Decimal("10"),
            gain_loss_eur=Decimal("10"),
            holding_seconds=-1,
            classification="Synthetischer Invariantentest",
            rule_version="journal-v1",
            status=TaxRecordStatus.RESOLVED,
            source_object_type="DisposalEvent",
            source_object_id=uuid4(),
        )


def test_tax_review_decision_history_is_deterministic_and_strict() -> None:
    valuation_id = uuid4()
    case_id = uuid4()
    batch_id = uuid4()
    first = TaxReviewDecision(
        valuation_decision_id=valuation_id,
        source_tax_review_case_id=case_id,
        decision=TaxReviewDecisionValue.INCLUDE_AS_WERBUNGSKOSTEN,
        reason="Beleg fachlich geprüft",
        actor_id="local-user",
        decided_at=NOW,
        version=1,
        batch_id=batch_id,
    )
    second = TaxReviewDecision(
        valuation_decision_id=valuation_id,
        source_tax_review_case_id=case_id,
        decision=TaxReviewDecisionValue.EXCLUDE_FROM_WERBUNGSKOSTEN,
        reason="Entscheidung nach erneuter Prüfung geändert",
        actor_id="local-user",
        decided_at=NOW + timedelta(minutes=1),
        version=2,
        supersedes_id=first.id,
        batch_id=uuid4(),
    )
    assert effective_tax_review_decisions([second, first]) == {valuation_id: second}
    broken = replace(second, supersedes_id=uuid4())
    with pytest.raises(ValueError, match="supersedes chain"):
        effective_tax_review_decisions([first, broken])
    with pytest.raises(ValueError, match="supersedes"):
        replace(first, version=2)
    skipped = replace(second, version=3)
    with pytest.raises(ValueError, match="consecutive"):
        effective_tax_review_decisions([first, skipped])


def test_csv_pdf_and_export_path_contract(tmp_path: Path) -> None:
    content = csv_bytes(
        ("id", "amount", "occurred_at"),
        [{"id": "ä", "amount": Decimal("1.2300"), "occurred_at": NOW}],
    )
    assert content.decode() == (
        "id;amount;occurred_at\nä;1.2300;2026-07-30T12:00:00+00:00\n"
    )
    run_id = str(uuid4())
    reason = (
        "Von Kraken unmittelbar von der jeweiligen Staking-Prämie einbehaltene "
        "Plattformprovision; Höhe anhand der dokumentierten Brutto-, Gebühren- "
        "und Netto-Reward-Komponenten nachvollzogen."
    )
    report = tax_report_pdf(
        TaxReportData(
            run_id=run_id,
            status="completed",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            created_at=NOW,
            snapshot_hash="a" * 64,
            rules_fingerprint="b" * 64,
            rules=TaxRuleVersion(),
            gross_staking_income=Decimal("51.586220962002859504121206557501435344829"),
            staking_fee_included=Decimal("11.964719979423988667047664309258439466617"),
            reviewed_net_staking_income=Decimal(
                "39.621500982578870837073542248242995878212"
            ),
            earn_inflows=55,
            disposals=0,
            allocations=0,
            asset_rows=tuple(
                ReportAssetRow(
                    asset=asset,
                    inflows=1,
                    gross_eur=Decimal("0.0000001") if asset == "BTC" else Decimal("1"),
                    fee_eur=Decimal("0") if asset == "BTC" else Decimal("0.1"),
                    net_eur=Decimal("0.0000001") if asset == "BTC" else Decimal("0.9"),
                )
                for asset in (
                    "ADA",
                    "ATOM",
                    "BTC",
                    "DOT",
                    "EIGEN",
                    "ETH",
                    "GRT",
                    "KAVA",
                    "XTZ",
                )
            ),
            inventory_rows=(
                ReportInventoryRow(
                    asset="BTC",
                    quantity=Decimal("0.00000001"),
                    cost_eur=Decimal("0.0000001"),
                ),
                ReportInventoryRow(
                    asset="KAVA",
                    quantity=Decimal("39.6215"),
                    cost_eur=Decimal("39.621500982578870837073542248242995878212"),
                ),
            ),
            review=ReportReviewEvidence(
                candidates=48,
                included=48,
                excluded=0,
                open_count=0,
                included_eur=Decimal("11.964719979423988667047664309258439466617"),
                batch_ids=(str(uuid4()),),
                versions=(1,),
                actors=("local-user",),
                decided_from=NOW,
                decided_to=NOW,
                reasons=(reason,),
            ),
        )
    )
    assert report.startswith(b"%PDF-")
    reader = PdfReader(io.BytesIO(report))
    assert 3 <= len(reader.pages) <= 4
    page_texts = [page.extract_text() or "" for page in reader.pages]
    for page_number, page_text in enumerate(page_texts, start=1):
        assert f"Seite {page_number} von {len(page_texts)}" in page_text
    text = " ".join("\n".join(page_texts).split())
    for expected in (
        "Steuerliche Arbeitsdokumentation 2026",
        run_id,
        "COMPLETED",
        PDF_REPORT_VERSION,
        "51,59 €",
        "11,96 €",
        "39,62 €",
        "55 Earn-Zuflüsse",
        "48 manuell geprüfte Plattformgebühren",
        reason,
        "fifo-utc-stable-v1",
        "proportional-last-remainder-v1",
        "keine erfassten Veräußerungen",
        "keine FIFO-Zuordnungen erforderlich",
        "Bestandsübersicht",
        "keine Steuerberatung",
        "Gebührenregel",
        "Prüfung der Staking-Plattformgebühren",
        "Seite 1 von",
    ):
        assert expected in text
    assert safe_export_path(tmp_path, "report.csv") == tmp_path / "report.csv"
    for unsafe in ("../report.csv", "/tmp/report.csv", "", "."):
        with pytest.raises(ValueError, match="safe base name"):
            safe_export_path(tmp_path, unsafe)
    linked = tmp_path / "linked.csv"
    linked.symlink_to(tmp_path.parent / "outside.csv")
    with pytest.raises(ValueError, match="escapes"):
        safe_export_path(tmp_path, linked.name)
    assert export_extension(ExportKind.TAX_REPORT_PDF) == (
        "pdf",
        "application/pdf",
    )
    assert export_extension(ExportKind.TAX_JOURNAL_CSV)[0] == "csv"


def test_export_rows_require_a_list_of_string_keyed_objects() -> None:
    source: list[dict[str, object]] = [{"id": "one", "amount": Decimal("1.25")}]
    result = _dict_list(source)
    assert result == source
    assert result is not source
    assert result[0] is not source[0]
    for invalid, message in (
        ({"id": "one"}, "Liste"),
        (["not-an-object"], "Objekt"),
        ([{1: "not-a-text-key"}], "Textschlüssel"),
    ):
        with pytest.raises(ValueError, match=message):
            _dict_list(invalid)


@pytest.fixture
def tax_client(tmp_path: Path) -> TestClient:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)

    def dependency() -> object:
        with sessions() as session:
            yield session

    settings = get_settings()
    previous_directory = settings.export_directory
    settings.export_directory = str(tmp_path)
    app.dependency_overrides[get_session] = dependency
    with TestClient(app) as client:
        yield client
    settings.export_directory = previous_directory
    app.dependency_overrides.clear()


@pytest.fixture
def tax_review_client(
    tmp_path: Path,
) -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as database:
        _seed_precise_reward_decisions(database)

    def dependency() -> object:
        with sessions() as database:
            yield database

    settings = get_settings()
    previous_directory = settings.export_directory
    settings.export_directory = str(tmp_path)
    app.dependency_overrides[get_session] = dependency
    try:
        with TestClient(app) as client:
            created = client.post("/api/tax-calculations", json={"year": 2026})
            assert created.status_code == 200
            yield client, sessions
    finally:
        settings.export_directory = previous_directory
        app.dependency_overrides.clear()


PRECISE_GROSS_TOTAL = Decimal("51.586220962002859504121206557501435344829")
PRECISE_FEE_TOTAL = Decimal("11.964719979423988667047664309258439466617")
PRECISE_NET_TOTAL = Decimal("39.621500982578870837073542248242995878212")


def _precise_parts(total: Decimal, count: int) -> tuple[Decimal, ...]:
    small = Decimal("0.000000000000000000000000000000000000001")
    leading = (small,) * (count - 1)
    return (
        *leading,
        exact_decimal_sum((total, *(item.copy_negate() for item in leading))),
    )


def _seed_precise_reward_decisions(database: Session) -> None:
    net_values = _precise_parts(PRECISE_NET_TOTAL, 55)
    fee_values = (*_precise_parts(PRECISE_FEE_TOTAL, 48), *(Decimal("0"),) * 7)
    transformation = TransformationRun(
        contract_version="kraken-domain-v2",
        status=TransformationStatus.COMPLETED,
        started_at=NOW,
        completed_at=NOW,
        actor_id="test-suite",
        checked_records=55,
        created_objects=55,
    )
    valuation_run = ValuationRun(
        provider="manual",
        correlation_id=uuid4(),
        started_at=NOW,
        ended_at=NOW,
        status=ValuationRunStatus.COMPLETED,
        checked_requirements=55,
        resolved_requirements=55,
        manual_count=55,
    )
    database.add_all((transformation, valuation_run))
    values = zip(net_values, fee_values, strict=True)
    for index, (net_value, fee_value) in enumerate(values):
        gross_value = exact_decimal_sum((net_value, fee_value))
        lot = AcquisitionLot(
            stable_key=f"precise-tax-reward-{index}",
            payload_hash=f"{index:064x}",
            asset_raw_code="KAVA",
            asset_code="KAVA",
            asset_mapping_version="kraken-assets-v2",
            quantity=net_value,
            gross_quantity=gross_value,
            fee_quantity=fee_value,
            fee_asset="KAVA" if fee_value else None,
            occurred_at=NOW - timedelta(minutes=index),
            acquisition_type=AcquisitionType.STAKING_REWARD,
            provider="kraken",
            account_scope="default",
            wallet_scope="kraken-spot",
            external_id=f"precise-tax-reward-{index}",
            transformation_version="kraken-domain-v2",
            valuation_status=ValuationStatus.VALUATION_REQUIRED,
            tax_treatment_hint=TaxTreatmentHint.PASSIVE_STAKING_REWARD,
        )
        requirement = ValuationRequirement(
            asset_code="KAVA",
            target_currency="EUR",
            valuation_at=lot.occurred_at,
            method=ValuationMethod.DAILY_AVERAGE,
            status=ValuationStatus.VALUATION_REQUIRED,
            reason_code="reward_inflow",
            domain_object_type="AcquisitionLot",
            domain_object_id=lot.id,
            transformation_run_id=transformation.id,
        )
        has_fee = fee_value > 0
        decision = ValuationDecision(
            valuation_requirement_id=requirement.id,
            valuation_run_id=valuation_run.id,
            domain_object_type="AcquisitionLot",
            domain_object_id=lot.id,
            asset_code="KAVA",
            quantity=net_value,
            valuation_at=lot.occurred_at,
            price_date=lot.occurred_at.date(),
            method=PriceMethod.MANUAL_DAILY_PRICE,
            unit_price_eur=Decimal("1"),
            eur_value=net_value,
            price_source="Synthetischer Präzisionstest",
            provider="manual",
            provider_object_id=None,
            provider_contract_version="manual-v1",
            method_version="eur-valuation-v2",
            sample_count=1,
            fetched_at=NOW,
            decided_at=NOW,
            status=ValuationDecisionStatus.RESOLVED,
            reason_code="valuation_resolved",
            gross_quantity=gross_value,
            fee_quantity=fee_value,
            net_quantity=net_value,
            gross_income_eur=gross_value,
            fee_value_eur=fee_value,
            net_acquisition_value_eur=net_value,
            valuation_basis="staking_reward_components_v2",
            fee_tax_classification=(
                FeeTaxClassification.WERBUNGSKOSTEN_CANDIDATE
                if has_fee
                else FeeTaxClassification.NOT_APPLICABLE
            ),
            fee_tax_review_status=(
                FeeTaxReviewStatus.REVIEW_REQUIRED
                if has_fee
                else FeeTaxReviewStatus.NOT_REQUIRED
            ),
        )
        database.add_all((lot, requirement, decision))
    database.commit()


def _assert_api_error(response: Response, status: int, code: str) -> None:
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code


def _second_historical_tax_run(database: Session, first_run_id: UUID) -> UUID:
    first_run = database.get(TaxCalculationRun, first_run_id)
    assert first_run is not None
    second_run = TaxCalculationRun(
        period_start=first_run.period_start,
        period_end=first_run.period_end,
        snapshot_hash="e" * 64,
        rules_fingerprint="f" * 64,
        status=TaxRunStatus.COMPLETED_WITH_REVIEW,
        started_at=first_run.started_at + timedelta(seconds=1),
        ended_at=first_run.started_at + timedelta(seconds=2),
        checked_events=first_run.checked_events,
        review_count=1,
    )
    database.add(second_run)
    database.flush()
    return second_run.id


def test_export_format_versions_preserve_legacy_pdf_and_domain_records(
    tax_review_client: tuple[TestClient, sessionmaker[Session]], tmp_path: Path
) -> None:
    client, sessions = tax_review_client
    legacy_content = b"%PDF-1.4\nlegacy-v1\n%%EOF\n"
    legacy_name = "legacy-tax-report-v1.pdf"
    (tmp_path / legacy_name).write_bytes(legacy_content)
    with sessions() as database:
        tax_run = database.scalars(select(TaxCalculationRun)).first()
        assert tax_run is not None
        legacy_run = ExportRun(
            tax_calculation_run_id=tax_run.id,
            kind=ExportKind.TAX_REPORT_PDF,
            status=ExportStatus.COMPLETED,
            period_start=tax_run.period_start,
            period_end=tax_run.period_end,
            rules_fingerprint=tax_run.rules_fingerprint,
            format_version="tax-report-pdf-v1",
            started_at=NOW,
            completed_at=NOW,
        )
        legacy_artifact = ExportArtifact(
            export_run_id=legacy_run.id,
            kind=ExportKind.TAX_REPORT_PDF,
            file_name=legacy_name,
            media_type="application/pdf",
            size_bytes=len(legacy_content),
            sha256_hash=sha256(legacy_content).hexdigest(),
            created_at=NOW,
        )
        database.add_all((legacy_run, legacy_artifact))
        database.commit()
        before = {
            "runs": database.scalar(
                select(func.count()).select_from(TaxCalculationRun)
            ),
            "decisions": database.scalar(
                select(func.count()).select_from(TaxReviewDecision)
            ),
            "journal": database.scalar(
                select(func.count()).select_from(TaxJournalEntry)
            ),
            "inventory": database.scalar(
                select(func.count()).select_from(InventoryLot)
            ),
        }

    request = {"tax_calculation_run_id": str(tax_run.id), "kind": "tax_report_pdf"}
    created = client.post("/api/exports", json=request)
    assert created.status_code == 200
    assert created.json()["duplicate"] is False
    assert created.json()["format_version"] == "tax-report-pdf-v2"
    assert created.json()["artifact_id"] != str(legacy_artifact.id)
    duplicate = client.post("/api/exports", json=request)
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["artifact_id"] == created.json()["artifact_id"]
    old_download = client.get(f"/api/exports/{legacy_artifact.id}/download")
    assert old_download.content == legacy_content

    csv_request = {
        "tax_calculation_run_id": str(tax_run.id),
        "kind": "tax_journal_csv",
    }
    first_csv = client.post("/api/exports", json=csv_request).json()
    assert first_csv["format_version"] == "tax-journal-csv-v1"
    second_csv = client.post("/api/exports", json=csv_request).json()
    assert second_csv["duplicate"] is True
    assert second_csv["artifact_id"] == first_csv["artifact_id"]
    listing = client.get("/api/exports?year=2026").json()
    assert {item["format_version"] for item in listing["items"]} >= {
        "tax-report-pdf-v1",
        "tax-report-pdf-v2",
        "tax-journal-csv-v1",
    }
    detail = client.get(f"/api/exports/{created.json()['artifact_id']}").json()
    assert detail["format_version"] == "tax-report-pdf-v2"
    with sessions() as database:
        after = {
            "runs": database.scalar(
                select(func.count()).select_from(TaxCalculationRun)
            ),
            "decisions": database.scalar(
                select(func.count()).select_from(TaxReviewDecision)
            ),
            "journal": database.scalar(
                select(func.count()).select_from(TaxJournalEntry)
            ),
            "inventory": database.scalar(
                select(func.count()).select_from(InventoryLot)
            ),
        }
        assert after == before
        assert (
            database.get(ExportArtifact, legacy_artifact.id).sha256_hash
            == sha256(legacy_content).hexdigest()
        )


def test_tax_review_domain_decision_reference_invariants() -> None:
    candidate = acquisition("1", "1", NOW, kind="staking_reward")
    with pytest.raises(
        ValueError, match="Review decision and its identifier must be complete"
    ):
        replace(
            candidate,
            platform_fee_decision=(TaxReviewDecisionValue.INCLUDE_AS_WERBUNGSKOSTEN),
        )
    with pytest.raises(ValueError, match="Review decision version must be positive"):
        replace(
            candidate,
            platform_fee_decision=(TaxReviewDecisionValue.INCLUDE_AS_WERBUNGSKOSTEN),
            tax_review_decision_id=uuid4(),
            tax_review_decision_version=0,
        )
    with pytest.raises(ValueError, match="Review decision version must be positive"):
        TaxReviewDecision(
            valuation_decision_id=uuid4(),
            source_tax_review_case_id=uuid4(),
            decision=TaxReviewDecisionValue.EXCLUDE_FROM_WERBUNGSKOSTEN,
            reason="Synthetische Prüfung",
            actor_id="local-user",
            decided_at=NOW,
            version=0,
            batch_id=uuid4(),
        )


def test_tax_review_api_rejects_inconsistent_decision_history(
    tax_review_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, sessions = tax_review_client
    with sessions() as database:
        valuation = database.scalars(select(ValuationDecision)).first()
        review_case = database.scalars(select(TaxReviewCase)).first()
        assert valuation is not None
        assert review_case is not None
        database.add(
            TaxReviewDecision(
                valuation_decision_id=valuation.id,
                source_tax_review_case_id=review_case.id,
                decision=TaxReviewDecisionValue.INCLUDE_AS_WERBUNGSKOSTEN,
                reason="Historie mit absichtlicher Versionslücke",
                actor_id="local-user",
                decided_at=NOW,
                version=2,
                supersedes_id=uuid4(),
                batch_id=uuid4(),
            )
        )
        database.commit()
        count_before = database.scalar(
            select(func.count()).select_from(TaxReviewDecision)
        )

    response = client.get("/api/tax-review-decisions")
    _assert_api_error(response, 409, "tax_review_decision_history_inconsistent")
    with sessions() as database:
        assert (
            database.scalar(select(func.count()).select_from(TaxReviewDecision))
            == count_before
        )


def test_tax_review_write_validation_and_bulk_duplicates(
    tax_review_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, sessions = tax_review_client
    with sessions() as database:
        valid_case = database.scalars(select(TaxReviewCase)).first()
        assert valid_case is not None
        current = database.get(ValuationDecision, valid_case.source_object_id)
        assert current is not None
        run_id = valid_case.tax_calculation_run_id
        wrong_kind = TaxReviewCase(
            tax_calculation_run_id=run_id,
            code="tax_other_review",
            message="Anderer fachlicher Prüffall",
            source_object_type="ValuationDecision",
            source_object_id=current.id,
            occurred_at=NOW,
        )
        missing_valuation = TaxReviewCase(
            tax_calculation_run_id=run_id,
            code="tax_staking_platform_fee_candidate_review",
            message="Bewertung fehlt absichtlich",
            source_object_type="ValuationDecision",
            source_object_id=uuid4(),
            occurred_at=NOW,
        )
        no_candidate = database.scalars(
            select(ValuationDecision).where(
                ValuationDecision.fee_tax_review_status
                == FeeTaxReviewStatus.NOT_REQUIRED
            )
        ).first()
        assert no_candidate is not None
        no_candidate_case = TaxReviewCase(
            tax_calculation_run_id=run_id,
            code="tax_staking_platform_fee_candidate_review",
            message="Keine Gebührenkomponente",
            source_object_type="ValuationDecision",
            source_object_id=no_candidate.id,
            occurred_at=NOW,
        )
        historical_run_id = _second_historical_tax_run(database, run_id)
        duplicate_valuation_case = TaxReviewCase(
            tax_calculation_run_id=historical_run_id,
            code="tax_staking_platform_fee_candidate_review",
            message="Historischer zweiter Prüffall",
            source_object_type="ValuationDecision",
            source_object_id=current.id,
            occurred_at=NOW + timedelta(seconds=1),
        )
        database.add_all(
            (wrong_kind, missing_valuation, no_candidate_case, duplicate_valuation_case)
        )
        database.commit()

    request = {
        "decision": "include_as_werbungskosten",
        "reason": "Synthetische Vertragsprüfung",
    }
    for case_id, status, code in (
        (wrong_kind.id, 409, "tax_review_case_not_decidable_as_staking_fee"),
        (missing_valuation.id, 404, "valuation_decision_not_found"),
        (no_candidate_case.id, 409, "tax_review_valuation_not_fee_candidate"),
    ):
        response = client.post(
            "/api/tax-review-decisions",
            json={**request, "tax_review_case_id": str(case_id)},
        )
        _assert_api_error(response, status, code)

    duplicate_cases = client.post(
        "/api/tax-review-decisions/bulk",
        json={
            **request,
            "tax_review_case_ids": [str(valid_case.id), str(valid_case.id)],
        },
    )
    _assert_api_error(duplicate_cases, 422, "tax_review_duplicate_case_ids")
    duplicate_valuations = client.post(
        "/api/tax-review-decisions/bulk",
        json={
            **request,
            "tax_review_case_ids": [
                str(valid_case.id),
                str(duplicate_valuation_case.id),
            ],
        },
    )
    _assert_api_error(duplicate_valuations, 422, "tax_review_duplicate_valuation_ids")
    with sessions() as database:
        assert database.scalar(select(func.count()).select_from(TaxReviewDecision)) == 0
        assert (
            database.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.event_type == "tax.review_decision_created")
            )
            == 0
        )


def test_tax_review_rejects_superseded_valuation(
    tax_review_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, sessions = tax_review_client
    with sessions() as database:
        review_case = database.scalars(select(TaxReviewCase)).first()
        assert review_case is not None
        old = database.get(ValuationDecision, review_case.source_object_id)
        assert old is not None
        database.add(
            replace(
                old,
                id=uuid4(),
                version=old.version + 1,
                supersedes_id=old.id,
                decided_at=old.decided_at + timedelta(seconds=1),
            )
        )
        database.commit()

    response = client.post(
        "/api/tax-review-decisions",
        json={
            "tax_review_case_id": str(review_case.id),
            "decision": "include_as_werbungskosten",
            "reason": "Alte Bewertung darf nicht entschieden werden",
        },
    )
    _assert_api_error(response, 409, "tax_review_valuation_superseded")
    with sessions() as database:
        assert database.scalar(select(func.count()).select_from(TaxReviewDecision)) == 0


def test_tax_review_list_filters_and_skips_non_contract_rows(
    tax_review_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, sessions = tax_review_client
    with sessions() as database:
        valid_case = database.scalars(select(TaxReviewCase)).first()
        assert valid_case is not None
        valuation = database.get(ValuationDecision, valid_case.source_object_id)
        assert valuation is not None
        run_id = valid_case.tax_calculation_run_id
        historical_run_id = _second_historical_tax_run(database, run_id)
        database.add_all(
            (
                TaxReviewCase(
                    tax_calculation_run_id=run_id,
                    code="tax_other_review",
                    message="Nicht anzeigbarer Prüffall",
                    source_object_type="ValuationDecision",
                    source_object_id=valuation.id,
                    occurred_at=NOW - timedelta(days=1),
                ),
                TaxReviewCase(
                    tax_calculation_run_id=run_id,
                    code="tax_staking_platform_fee_candidate_review",
                    message="Fehlende Bewertung",
                    source_object_type="ValuationDecision",
                    source_object_id=uuid4(),
                    occurred_at=NOW - timedelta(seconds=1),
                ),
                TaxReviewCase(
                    tax_calculation_run_id=historical_run_id,
                    code="tax_staking_platform_fee_candidate_review",
                    message="Doppelter historischer Prüffall",
                    source_object_type="ValuationDecision",
                    source_object_id=valuation.id,
                    occurred_at=NOW + timedelta(days=1),
                ),
            )
        )
        database.commit()

    resolved = client.post(
        "/api/tax-review-decisions",
        json={
            "tax_review_case_id": str(valid_case.id),
            "decision": "include_as_werbungskosten",
            "reason": "Synthetische Filterprüfung",
        },
    )
    assert resolved.status_code == 200
    assert client.get("/api/tax-review-decisions?year=2025").json()["total"] == 0
    open_page = client.get("/api/tax-review-decisions?status=open&limit=100").json()
    assert open_page["total"] == 47
    assert all(item["status"] == "open" for item in open_page["items"])
    assert client.get("/api/tax-review-decisions?asset=BTC").json()["total"] == 0
    assert (
        client.get(
            "/api/tax-review-decisions?decision=exclude_from_werbungskosten"
        ).json()["total"]
        == 0
    )
    included = client.get(
        "/api/tax-review-decisions?decision=include_as_werbungskosten"
    ).json()
    assert included["total"] == 1
    assert included["items"][0]["valuation_decision_id"] == str(valuation.id)


def test_tax_api_empty_validation_and_openapi(tax_client: TestClient) -> None:
    for path in (
        "/api/tax-calculations",
        "/api/inventory-lots",
        "/api/lot-allocations",
        "/api/tax-journal",
        "/api/exports",
        "/api/tax-review-decisions",
    ):
        response = tax_client.get(path)
        assert response.status_code == 200
        assert response.json()["total"] == 0
    summary = tax_client.get("/api/tax-summary?year=2026")
    assert summary.status_code == 200
    assert summary.json()["net_result"] == "0"
    assert summary.json()["inventory"] == {}
    assert summary.json()["gross_staking_income"] == "0"
    assert summary.json()["staking_fee_candidates"] == "0"
    assert summary.json()["provisional_net_staking_income"] == "0"
    assert summary.json()["staking_fee_included"] == "0"
    assert summary.json()["staking_fee_excluded"] == "0"
    assert summary.json()["staking_fee_open"] == "0"
    assert summary.json()["reviewed_net_staking_income"] == "0"
    random_id = uuid4()
    for path in (
        f"/api/tax-calculations/{random_id}",
        f"/api/inventory-lots/{random_id}",
        f"/api/lot-allocations/{random_id}",
        f"/api/tax-journal/{random_id}",
        f"/api/exports/{random_id}",
        f"/api/exports/{random_id}/download",
    ):
        assert tax_client.get(path).status_code == 404
    missing_run_export = tax_client.post(
        "/api/exports",
        json={
            "tax_calculation_run_id": str(random_id),
            "kind": ExportKind.TAX_JOURNAL_CSV.value,
        },
    )
    assert missing_run_export.status_code == 404
    assert missing_run_export.json()["detail"] == {
        "code": "tax_calculation_not_found",
        "message": "Der Datensatz wurde nicht gefunden.",
    }
    assert (
        tax_client.post("/api/tax-calculations", json={"year": 1969}).status_code == 422
    )
    schema = tax_client.get("/openapi.json").json()
    for path in (
        "/api/tax-calculations",
        "/api/inventory-lots",
        "/api/lot-allocations",
        "/api/tax-journal",
        "/api/tax-summary",
        "/api/exports",
        "/api/exports/{item_id}/download",
        "/api/tax-review-decisions",
        "/api/tax-review-decisions/bulk",
    ):
        assert path in schema["paths"]


def test_precise_reward_tax_run_is_exact_persistent_and_idempotent() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as database:
        _seed_precise_reward_decisions(database)

    def dependency() -> object:
        with sessions() as database:
            yield database

    context_before = getcontext().copy()
    app.dependency_overrides[get_session] = dependency
    try:
        with TestClient(app) as client:
            created = client.post("/api/tax-calculations", json={"year": 2026})
            assert created.status_code == 200
            body = created.json()
            assert body == {
                "id": body["id"],
                "status": "completed_with_review",
                "checked": 103,
                "allocations": 0,
                "journal_entries": 103,
                "reviews": 48,
                "duplicate": False,
            }
            repeated = client.post("/api/tax-calculations", json={"year": 2026})
            assert repeated.status_code == 200
            assert repeated.json()["id"] == body["id"]
            assert repeated.json()["duplicate"] is True
            summary = client.get("/api/tax-summary?year=2026")
            assert summary.status_code == 200
            summary_body = summary.json()
    finally:
        app.dependency_overrides.clear()

    assert_decimal_context_unchanged(context_before)
    assert Decimal(summary_body["gross_staking_income"]) == PRECISE_GROSS_TOTAL
    assert Decimal(summary_body["staking_fee_candidates"]) == PRECISE_FEE_TOTAL
    assert Decimal(summary_body["provisional_net_staking_income"]) == (
        PRECISE_NET_TOTAL
    )
    assert (
        exact_decimal_sum((PRECISE_NET_TOTAL, PRECISE_FEE_TOTAL)) == PRECISE_GROSS_TOTAL
    )
    assert summary_body["earn_inflows"] == 55
    assert summary_body["open_reviews"] == 48
    assert summary_body["disposals"] == 0
    assert summary_body["incomplete_disposals"] == 0
    assert Decimal(summary_body["inventory"]["KAVA"]) == PRECISE_NET_TOTAL

    with sessions() as database:
        assert database.scalar(select(func.count()).select_from(TaxCalculationRun)) == 1
        assert database.scalar(select(func.count()).select_from(InventoryLot)) == 55
        assert database.scalar(select(func.count()).select_from(LotAllocation)) == 0
        assert database.scalar(select(func.count()).select_from(TaxJournalEntry)) == 103
        assert database.scalar(select(func.count()).select_from(TaxReviewCase)) == 48
        lots = tuple(database.scalars(select(InventoryLot)))
        journal = tuple(database.scalars(select(TaxJournalEntry)))
        reviews = tuple(database.scalars(select(TaxReviewCase)))
        assert all(item.original_quantity == item.remaining_quantity for item in lots)
        assert all(
            item.acquisition_value_eur == item.remaining_cost_eur for item in lots
        )
        assert exact_decimal_sum(tuple(item.remaining_cost_eur for item in lots)) == (
            PRECISE_NET_TOTAL
        )
        assert (
            sum(item.entry_type is JournalEntryType.EARN_INFLOW for item in journal)
            == 55
        )
        assert sum(item.entry_type is JournalEntryType.REVIEW for item in journal) == 48
        assert sum(item.status is TaxRecordStatus.RESOLVED for item in journal) == 55
        assert (
            sum(item.status is TaxRecordStatus.REVIEW_REQUIRED for item in journal)
            == 48
        )
        assert {item.code for item in reviews} == {
            "tax_staking_platform_fee_candidate_review"
        }


def test_staking_fee_review_bulk_versions_tax_runs_and_summary(tmp_path: Path) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as database:
        _seed_precise_reward_decisions(database)

    def dependency() -> object:
        with sessions() as database:
            yield database

    context_before = getcontext().copy()
    settings = get_settings()
    previous_directory = settings.export_directory
    settings.export_directory = str(tmp_path)
    app.dependency_overrides[get_session] = dependency
    try:
        with TestClient(app) as client:
            first_run = client.post("/api/tax-calculations", json={"year": 2026})
            assert first_run.status_code == 200
            assert first_run.json()["status"] == "completed_with_review"
            open_page = client.get(
                "/api/tax-review-decisions?year=2026&status=open&limit=100"
            )
            assert open_page.status_code == 200
            open_items = open_page.json()["items"]
            assert len(open_items) == 48
            case_ids = [item["tax_review_case_id"] for item in open_items]

            invalid = client.post(
                "/api/tax-review-decisions/bulk",
                json={
                    "tax_review_case_ids": [*case_ids[:-1], str(uuid4())],
                    "decision": "include_as_werbungskosten",
                    "reason": "Synthetische gemeinsame Belegprüfung",
                },
            )
            assert invalid.status_code == 404
            with sessions() as database:
                assert (
                    database.scalar(select(func.count()).select_from(TaxReviewDecision))
                    == 0
                )
                assert (
                    database.scalar(
                        select(func.count())
                        .select_from(AuditEvent)
                        .where(AuditEvent.event_type == "tax.review_decision_created")
                    )
                    == 0
                )

            included = client.post(
                "/api/tax-review-decisions/bulk",
                json={
                    "tax_review_case_ids": case_ids,
                    "decision": "include_as_werbungskosten",
                    "reason": "Synthetische gemeinsame Belegprüfung",
                },
            )
            assert included.status_code == 200
            assert included.json()["created_count"] == 48
            assert included.json()["superseded_count"] == 0
            with sessions() as database:
                persisted = database.scalars(
                    select(TaxReviewDecision).order_by(TaxReviewDecision.id)
                ).first()
                assert persisted is not None
                persisted_id = persisted.id
                persisted_reason = persisted.reason
                persisted.reason = "Unzulässige Mutation"
                with pytest.raises(ValueError, match="Immutable records"):
                    database.commit()
                database.rollback()
            with sessions() as database:
                unchanged = database.get(TaxReviewDecision, persisted_id)
                assert unchanged is not None
                assert unchanged.reason == persisted_reason
            second_run = client.post("/api/tax-calculations", json={"year": 2026})
            assert second_run.status_code == 200
            assert second_run.json()["duplicate"] is False
            assert second_run.json()["status"] == "completed"
            assert second_run.json()["reviews"] == 0
            second_summary = client.get("/api/tax-summary?year=2026").json()
            assert Decimal(second_summary["staking_fee_included"]) == PRECISE_FEE_TOTAL
            assert Decimal(second_summary["staking_fee_excluded"]) == 0
            assert Decimal(second_summary["staking_fee_open"]) == 0
            assert (
                Decimal(second_summary["reviewed_net_staking_income"])
                == PRECISE_NET_TOTAL
            )
            exported = client.post(
                "/api/exports",
                json={
                    "tax_calculation_run_id": second_run.json()["id"],
                    "kind": "reviews_csv",
                },
            )
            assert exported.status_code == 200
            review_csv = client.get(exported.json()["download_url"])
            assert review_csv.status_code == 200
            assert b"include_as_werbungskosten" in review_csv.content
            assert b"Synthetische gemeinsame Belegpr" in review_csv.content

            changed_case = case_ids[0]
            changed_value = Decimal(open_items[0]["fee_value_eur"])
            excluded = client.post(
                "/api/tax-review-decisions",
                json={
                    "tax_review_case_id": changed_case,
                    "decision": "exclude_from_werbungskosten",
                    "reason": "Synthetische geänderte Einzelentscheidung",
                },
            )
            assert excluded.status_code == 200
            assert excluded.json()["version"] == 2
            assert excluded.json()["supersedes_id"] is not None
            third_run = client.post("/api/tax-calculations", json={"year": 2026})
            assert third_run.status_code == 200
            assert third_run.json()["duplicate"] is False
            third_summary = client.get("/api/tax-summary?year=2026").json()
            assert Decimal(third_summary["staking_fee_included"]) == (
                exact_decimal_subtract(PRECISE_FEE_TOTAL, changed_value)
            )
            assert Decimal(third_summary["staking_fee_excluded"]) == changed_value
            assert Decimal(third_summary["staking_fee_open"]) == 0
            assert Decimal(third_summary["reviewed_net_staking_income"]) == (
                exact_decimal_sum((PRECISE_NET_TOTAL, changed_value))
            )
            history = client.get(
                "/api/tax-review-decisions?year=2026&status=resolved&limit=100"
            ).json()
            changed = next(
                item
                for item in history["items"]
                if item["tax_review_case_id"] == changed_case
            )
            assert [item["version"] for item in changed["history"]] == [1, 2]
    finally:
        settings.export_directory = previous_directory
        app.dependency_overrides.clear()

    assert_decimal_context_unchanged(context_before)
    with sessions() as database:
        decisions = tuple(database.scalars(select(TaxReviewDecision)))
        assert len(decisions) == 49
        assert len({item.batch_id for item in decisions if item.version == 1}) == 1
        assert all(item.version == 1 for item in decisions if item.version == 1)
        runs = tuple(database.scalars(select(TaxCalculationRun)))
        assert len(runs) == 3
        assert sum(item.status is TaxRunStatus.SUPERSEDED for item in runs) == 2
        latest = max(runs, key=lambda item: item.started_at)
        journal = tuple(
            database.scalars(
                select(TaxJournalEntry).where(
                    TaxJournalEntry.tax_calculation_run_id == latest.id
                )
            )
        )
        assert (
            sum(item.entry_type is JournalEntryType.EARN_INFLOW for item in journal)
            == 55
        )
        assert sum(item.entry_type is JournalEntryType.FEE for item in journal) == 47
        assert not any(item.entry_type is JournalEntryType.REVIEW for item in journal)
        lots = tuple(
            database.scalars(
                select(InventoryLot).where(
                    InventoryLot.tax_calculation_run_id == latest.id
                )
            )
        )
        assert exact_decimal_sum(tuple(item.remaining_cost_eur for item in lots)) == (
            PRECISE_NET_TOTAL
        )


def test_tax_workflow_rolls_back_all_records_after_unexpected_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as database:
        _seed_precise_reward_decisions(database)

    def dependency() -> object:
        with sessions() as database:
            yield database

    def fail_fifo(**_arguments: object) -> object:
        raise RuntimeError("synthetic tax workflow failure")

    monkeypatch.setattr("app.api.tax.calculate_fifo", fail_fifo)
    app.dependency_overrides[get_session] = dependency
    try:
        with (
            TestClient(app) as client,
            pytest.raises(RuntimeError, match="synthetic tax workflow failure"),
        ):
            client.post("/api/tax-calculations", json={"year": 2026})
    finally:
        app.dependency_overrides.clear()

    with sessions() as database:
        for model in (
            TaxCalculationRun,
            InventoryLot,
            LotAllocation,
            TaxJournalEntry,
            TaxReviewCase,
            AuditEvent,
        ):
            assert database.scalar(select(func.count()).select_from(model)) == 0


def test_tax_api_fifo_journal_exports_and_idempotency(tax_client: TestClient) -> None:
    trade_csv = (
        "txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol\n"
        "BUY,B1,XXBTZEUR,2026-01-02 03:04:05,buy,limit,100,200,2,2\n"
        "SELL,S1,XXBTZEUR,2026-02-02 03:04:05,sell,limit,150,150,1,1\n"
    )
    imported = tax_client.post(
        "/api/imports/kraken?transform=true",
        files={"file": ("trades.csv", trade_csv.encode())},
    )
    assert imported.status_code == 200
    valuation = tax_client.post("/api/valuations")
    assert valuation.status_code == 200
    assert valuation.json()["resolved"] >= 2
    calculated = tax_client.post("/api/tax-calculations", json={"year": 2026})
    assert calculated.status_code == 200
    body = calculated.json()
    assert body["status"] == "completed"
    assert body["allocations"] == 1
    duplicate = tax_client.post("/api/tax-calculations", json={"year": 2026})
    assert duplicate.json()["id"] == body["id"]
    assert duplicate.json()["duplicate"] is True

    lots = tax_client.get("/api/inventory-lots?year=2026").json()
    allocations = tax_client.get("/api/lot-allocations?year=2026").json()
    journal = tax_client.get("/api/tax-journal?year=2026").json()
    assert lots["items"][0]["remaining_quantity"] == "1"
    assert Decimal(allocations["items"][0]["gain_loss_eur"]) == Decimal("48")
    assert journal["total"] >= 4
    for collection, endpoint in (
        (lots, "inventory-lots"),
        (allocations, "lot-allocations"),
        (journal, "tax-journal"),
    ):
        detail = tax_client.get(f"/api/{endpoint}/{collection['items'][0]['id']}")
        assert detail.status_code == 200

    summary = tax_client.get("/api/tax-summary?year=2026").json()
    assert summary["disposals"] == 1
    assert Decimal(summary["net_result"]) == Decimal("48")
    run_id = body["id"]
    for kind in (item.value for item in ExportKind):
        created = tax_client.post(
            "/api/exports",
            json={"tax_calculation_run_id": run_id, "kind": kind},
        )
        assert created.status_code == 200
        exported = created.json()
        download = tax_client.get(exported["download_url"])
        assert download.status_code == 200
        if kind.endswith("pdf"):
            assert download.content.startswith(b"%PDF")
        else:
            assert b";" in download.content
        repeated = tax_client.post(
            "/api/exports",
            json={"tax_calculation_run_id": run_id, "kind": kind},
        )
        assert repeated.json()["artifact_id"] == exported["artifact_id"]
        assert repeated.json()["duplicate"] is True
    exports = tax_client.get("/api/exports?year=2026").json()
    assert exports["total"] == len(ExportKind)
    assert (
        tax_client.get(f"/api/exports/{exports['items'][0]['id']}").status_code == 200
    )
    missing_file = exports["items"][0]
    (Path(get_settings().export_directory) / missing_file["file_name"]).unlink()
    missing_download = tax_client.get(missing_file["download_url"])
    assert missing_download.status_code == 404
    assert missing_download.json()["detail"]["code"] == "export_file_not_found"
    assert get_settings().export_directory not in missing_download.text
    filtered = tax_client.get(
        "/api/tax-journal?year=2026&asset=BTC&status=resolved&review=false"
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] > 0
    recalculated = tax_client.post(
        "/api/tax-calculations",
        json={"year": 2026, "fifo_rule_version": "fifo-utc-stable-v2"},
    )
    assert recalculated.status_code == 200
    assert recalculated.json()["id"] != run_id
    old = tax_client.get(f"/api/tax-calculations/{run_id}").json()
    new = tax_client.get(f"/api/tax-calculations/{recalculated.json()['id']}").json()
    assert old["status"] == "superseded"
    assert new["supersedes_id"] == run_id


def test_tax_api_missing_valuation_and_crypto_fee_create_reviews(
    tax_client: TestClient,
) -> None:
    ledger = (
        "txid,time,type,asset,amount,fee,subtype\n"
        "REWARD,2026-03-02 03:04:05,earn,XXBT,1,0.01,reward\n"
    )
    imported = tax_client.post(
        "/api/imports/kraken?transform=true",
        files={"file": ("ledger.csv", ledger.encode())},
    )
    assert imported.status_code == 200
    crypto_trade = (
        "txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol\n"
        "CRYPTO,C1,XETHXXBT,2026-04-02 03:04:05,buy,limit,0.05,0.5,0.01,10\n"
    )
    imported_trade = tax_client.post(
        "/api/imports/kraken?transform=true",
        files={"file": ("crypto-trade.csv", crypto_trade.encode())},
    )
    assert imported_trade.status_code == 200
    calculated = tax_client.post("/api/tax-calculations", json={"year": 2026})
    assert calculated.status_code == 200
    assert calculated.json()["status"] == "completed_with_review"
    reviews = tax_client.get("/api/reviews").json()
    assert reviews["total"] >= 1
    assert any(item["kind"] == "tax" for item in reviews["items"])
    tax_review = next(item for item in reviews["items"] if item["kind"] == "tax")
    detail = tax_client.get(f"/api/reviews/{tax_review['id']}")
    assert detail.status_code == 200
    assert detail.json()["source_object_id"]
    journal = tax_client.get("/api/tax-journal?review=true").json()
    assert journal["total"] >= 1
    assert all(item["status"] == "review_required" for item in journal["items"])
    codes = {item["code"] for item in reviews["items"] if item["kind"] == "tax"}
    assert "tax_valuation_missing" in codes
    assert "tax_crypto_fee_requires_disposal_review" in codes


def test_tax_calculation_ignores_events_outside_reporting_year(
    tax_client: TestClient,
) -> None:
    trades = (
        "txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol\n"
        "OLD,S1,XXBTZEUR,2025-02-02 03:04:05,sell,limit,90,90,0,1\n"
        "FUTURE,B1,XXBTZEUR,2027-02-02 03:04:05,buy,limit,100,100,0,1\n"
    )
    imported = tax_client.post(
        "/api/imports/kraken?transform=true",
        files={"file": ("outside-period.csv", trades.encode())},
    )
    assert imported.status_code == 200
    calculated = tax_client.post("/api/tax-calculations", json={"year": 2026})
    assert calculated.status_code == 200
    assert calculated.json()["status"] == "completed"
    assert calculated.json()["checked"] == 0
    assert tax_client.get("/api/inventory-lots?year=2026").json()["total"] == 0
    assert tax_client.get("/api/lot-allocations?year=2026").json()["total"] == 0


def test_fifo_uses_inclusive_period_and_preperiod_inventory() -> None:
    period = TaxReportingPeriod.for_year(2026)
    before = acquisition("4", "400", datetime(2025, 12, 31, 23, 59, tzinfo=UTC))
    at_start = acquisition("1", "110", datetime(2026, 1, 1, tzinfo=UTC))
    at_end = acquisition("1", "120", datetime(2026, 12, 31, 23, 59, tzinfo=UTC))
    after = acquisition("1", "130", datetime(2027, 1, 1, tzinfo=UTC))
    before_sale = disposal("1", "150", datetime(2025, 12, 31, 23, 59, tzinfo=UTC))
    start_sale = disposal("1", "160", datetime(2026, 1, 1, tzinfo=UTC))
    end_sale = disposal("1", "170", datetime(2026, 12, 31, 23, 59, tzinfo=UTC))
    after_sale = disposal("1", "180", datetime(2027, 1, 1, tzinfo=UTC))
    fiat = AcquisitionInput(
        acquisition_id=uuid4(),
        asset_code="EUR",
        quantity=Decimal("10"),
        acquired_at=datetime(2026, 6, 1, tzinfo=UTC),
        value_eur=Decimal("10"),
        fee_eur=Decimal("0"),
        valuation_decision_id=uuid4(),
        acquisition_type="trade_sell",
    )

    result = calculate_fifo(
        run_id=uuid4(),
        period=period,
        rules=TaxRuleVersion(),
        acquisitions=[after, at_end, fiat, at_start, before],
        disposals=[after_sale, end_sale, start_sale, before_sale],
    )

    assert {lot.acquisition_lot_id for lot in result.lots} == {
        before.acquisition_id,
        at_start.acquisition_id,
        at_end.acquisition_id,
    }
    assert {item.disposal_event_id for item in result.calculations} == {
        start_sale.disposal_id,
        end_sale.disposal_id,
    }
    assert all(
        period.start <= item.occurred_at.date() <= period.end for item in result.journal
    )
    acquisition_entries = [
        item
        for item in result.journal
        if item.entry_type is JournalEntryType.ACQUISITION
    ]
    assert {item.source_object_id for item in acquisition_entries} == {
        at_start.acquisition_id,
        at_end.acquisition_id,
    }


def test_export_entities_reject_unsafe_names() -> None:
    export_run = ExportRun(
        tax_calculation_run_id=uuid4(),
        kind=ExportKind.TAX_JOURNAL_CSV,
        status=ExportStatus.CREATED,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        rules_fingerprint="a" * 64,
        format_version="tax-journal-csv-v1",
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
    )
    assert export_run.completed_at == NOW + timedelta(seconds=1)
    assert EXPORT_FORMAT_VERSIONS == {
        ExportKind.TAX_JOURNAL_CSV: "tax-journal-csv-v1",
        ExportKind.FIFO_ALLOCATIONS_CSV: "fifo-allocations-csv-v1",
        ExportKind.INVENTORY_CSV: "inventory-csv-v1",
        ExportKind.VALUATION_EVIDENCE_CSV: "valuation-evidence-csv-v1",
        ExportKind.REVIEWS_CSV: "reviews-csv-v1",
        ExportKind.ANNUAL_SUMMARY_CSV: "annual-summary-csv-v1",
        ExportKind.TAX_REPORT_PDF: "tax-report-pdf-v2",
    }
    with pytest.raises(ValueError, match="format_version"):
        replace(export_run, format_version="")
    with pytest.raises(ValueError, match="safe base name"):
        ExportArtifact(
            export_run_id=export_run.id,
            kind=ExportKind.TAX_JOURNAL_CSV,
            file_name="../unsafe.csv",
            media_type="text/csv",
            size_bytes=1,
            sha256_hash="a" * 64,
            created_at=NOW,
        )
    with pytest.raises(ValueError, match="size_bytes"):
        ExportArtifact(
            export_run_id=export_run.id,
            kind=ExportKind.TAX_JOURNAL_CSV,
            file_name="negative-size.csv",
            media_type="text/csv",
            size_bytes=-1,
            sha256_hash="a" * 64,
            created_at=NOW,
        )
    entry = TaxJournalEntry(
        tax_calculation_run_id=uuid4(),
        occurred_at=NOW,
        tax_year=2026,
        entry_type=JournalEntryType.CORRECTION,
        asset_code="EUR",
        quantity=Decimal("1"),
        eur_value=Decimal("0"),
        proceeds_eur=None,
        acquisition_cost_eur=None,
        gain_loss_eur=None,
        holding_seconds=None,
        classification="Korrekturhinweis",
        rule_version="v2",
        status=TaxRecordStatus.SUPERSEDED,
        source_object_type="TaxCalculationRun",
        source_object_id=UUID(int=0),
    )
    assert entry.entry_type is JournalEntryType.CORRECTION
