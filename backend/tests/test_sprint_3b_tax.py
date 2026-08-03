from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.tax import _dict_list
from app.config.settings import get_settings
from app.core.tax import (
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
    TaxRuleVersion,
    TaxRunStatus,
    calculate_fifo,
    tax_snapshot_hash,
)
from app.database.base import Base
from app.database.session import get_session
from app.main import app
from app.services.tax_exports import (
    csv_bytes,
    export_extension,
    safe_export_path,
    tax_report_pdf,
)

NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)


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

    result = calculate_fifo(
        run_id=run_id,
        period=TaxReportingPeriod.for_year(2026),
        rules=rules,
        acquisitions=[third, second, first],
        disposals=[sold],
    )

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


def test_csv_pdf_and_export_path_contract(tmp_path: Path) -> None:
    content = csv_bytes(
        ("id", "amount", "occurred_at"),
        [{"id": "ä", "amount": Decimal("1.2300"), "occurred_at": NOW}],
    )
    assert content.decode() == (
        "id;amount;occurred_at\nä;1.2300;2026-07-30T12:00:00+00:00\n"
    )
    report = tax_report_pdf(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        created_at=NOW,
        rules=TaxRuleVersion(),
        summary={"realized_gains": "10", "net_result": "10"},
    )
    assert report.startswith(b"%PDF-1.4")
    assert report.endswith(b"%%EOF\n")
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


def test_tax_api_empty_validation_and_openapi(tax_client: TestClient) -> None:
    for path in (
        "/api/tax-calculations",
        "/api/inventory-lots",
        "/api/lot-allocations",
        "/api/tax-journal",
        "/api/exports",
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
    ):
        assert path in schema["paths"]


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
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
    )
    assert export_run.completed_at == NOW + timedelta(seconds=1)
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
