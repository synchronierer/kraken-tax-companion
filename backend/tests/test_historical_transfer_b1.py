from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app import models
from app.adapters.historical_sources import (
    BITCOIN_DE_SOURCE,
    BITTREX_SOURCE,
    MANUAL_BOOKKEEPING_SOURCE,
    BitcoinDeStatementRecord,
    BittrexOrderHistoryRecord,
    parse_bitcoin_de_csv,
    parse_bitcoin_de_records,
    parse_bitcoin_de_statement,
    parse_bittrex_csv,
    parse_bittrex_order_history,
    parse_bittrex_records,
    parse_manual_transfer_evidence,
)
from app.adapters.kraken import transformation as kraken_transformation
from app.adapters.kraken.transformation import KrakenTransformationService
from app.core.entities import AuditActorType, ImportSession, ImportStatus
from app.core.historical_transfer import (
    HistoricalBasisCoverage,
    HistoricalResolutionStatus,
    HistoricalTransferLink,
    HistoricalTransferNature,
)
from app.core.identifiers import Uuid4IdGenerator, new_id
from app.core.tax import (
    AcquisitionInput,
    DisposalInput,
    TaxReportingPeriod,
    TaxRuleVersion,
    calculate_fifo,
)
from app.core.transformation import (
    AcquisitionLot,
    AssetIdentity,
    FeeEvent,
    MappingStatus,
    TransformationDecision,
    TransformationIssue,
    TransformationRun,
    TransformationStatus,
    ValuationRequirement,
)
from app.database.base import Base
from app.database.unit_of_work import SqlAlchemyUnitOfWork
from app.imports.context import ImportContext
from app.imports.service import ImportService, RawRecordInput
from app.imports.validation import RequiredFieldsValidator
from app.services import historical_transfer as historical_transfer_service
from app.services.historical_transfer import HistoricalTransferService

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)


def database_factory() -> sessionmaker[Session]:
    models.configure_mappings()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def import_rows(
    factory: sessionmaker[Session],
    source: str,
    rows: list[BitcoinDeStatementRecord | BittrexOrderHistoryRecord],
) -> tuple[UUID, object]:
    return import_inputs(factory, source, [row.raw_record() for row in rows])


def import_inputs(
    factory: sessionmaker[Session], source: str, inputs: list[RawRecordInput]
) -> tuple[UUID, object]:
    session = ImportSession(
        source=source,
        version="historical-source-v1",
        status=ImportStatus.CREATED,
        started_at=NOW,
        correlation_id=new_id(),
        actor_type=AuditActorType.SYSTEM,
        actor_id="test-suite",
    )
    context = ImportContext(
        session=session,
        source=source,
        version=session.version,
        received_at=NOW,
        actor_type=session.actor_type,
        actor_id=session.actor_id,
        correlation_id=session.correlation_id,
    )
    result = ImportService(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(factory),
        id_generator=Uuid4IdGenerator(),
        validator=RequiredFieldsValidator(frozenset()),
        clock=lambda: NOW,
    ).import_records(context=context, records=inputs)
    assert result.accepted_count + result.reused_count == len(inputs)
    return result.session_id, result


def raw_id(factory: sessionmaker[Session], external_id: str) -> UUID:
    with factory() as database:
        row = database.execute(
            select(models.RawImportRecord).where(
                models.RawImportRecord.external_id == external_id
            )
        ).scalar_one()
        return row.id


def resolve(
    factory: sessionmaker[Session],
    source_ids: tuple[UUID, ...],
    *,
    coverage: HistoricalBasisCoverage,
):
    return HistoricalTransferService(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(factory), clock=lambda: NOW
    ).resolve(
        source_record_ids=source_ids,
        canonical_asset="BTC",
        source_quantity=Decimal("1"),
        target_quantity=Decimal("0.9"),
        transfer_nature=HistoricalTransferNature.SELF_TRANSFER,
        basis_coverage=coverage,
        evidence_type="bitcoin-de-account-statement",
        explanation="Synthetic evidence for unit testing.",
    )


def test_bitcoin_de_parser_preserves_decimal_timestamp_and_identity() -> None:
    row = parse_bitcoin_de_statement(
        {
            "id": "SUGP63",
            "date": "2021-06-04T12:00:00+02:00",
            "type": "Kauf",
            "currency": "btc",
            "amount": "0.11111405",
            "cost": "1234.5600",
            "reference": "REF-1",
        }
    )
    assert row.record_type == "buy"
    assert row.occurred_at == datetime(2021, 6, 4, 10, tzinfo=UTC)
    assert row.quantity == Decimal("0.11111405")
    assert row.raw_record().canonical_key == "bitcoin-de:statement:SUGP63"
    assert row.raw_record().technical_metadata["source_kind"] == BITCOIN_DE_SOURCE


def test_bittrex_parser_accepts_order_uuid_and_exact_values() -> None:
    row = parse_bittrex_order_history(
        {
            "OrderUuid": "ORDER-1",
            "TimeStamp": "2021-06-04T10:00:00Z",
            "OrderType": "SELL",
            "Currency": "DOGE",
            "Quantity": "10.00000000",
            "Price": "0.5",
            "Total": "5.00000000",
            "Commission": "2",
            "Market": "BTC-DOGE",
        }
    )
    assert row.source_id == "ORDER-1"
    assert row.record_type == "sell"
    assert row.quantity == Decimal("10.00000000")
    assert row.canonical_key == "bittrex:order:ORDER-1"
    assert row.raw_record().technical_metadata["source_kind"] == BITTREX_SOURCE


def test_source_parsers_fail_closed_and_batch_wrappers_are_deterministic() -> None:
    import pytest

    with pytest.raises(ValueError, match="stable source_id"):
        parse_bitcoin_de_statement({"type": "buy"})
    with pytest.raises(ValueError, match="stable order UUID"):
        parse_bittrex_order_history({"type": "buy"})
    bitcoin = parse_bitcoin_de_statement(
        {
            "id": "BATCH-1",
            "date": "not-a-date",
            "type": "buy",
            "asset": "BTC",
            "amount": "NaN",
            "cost": "Infinity",
            "fee": 0.1,
        }
    )
    bittrex = parse_bittrex_order_history(
        {
            "OrderUuid": "BATCH-2",
            "TimeStamp": "2021-01-01T00:00:00Z",
            "OrderType": "mystery",
            "Currency": "DOGE",
            "Quantity": "1",
        }
    )
    assert bitcoin.occurred_at is None
    assert bitcoin.quantity is None
    assert bitcoin.cost_eur is None
    assert bitcoin.fee_quantity is None
    invalid_decimal = parse_bitcoin_de_statement(
        {"id": "INVALID", "type": "buy", "cost": "abc"}
    )
    assert invalid_decimal.cost_eur is None
    assert len(parse_bitcoin_de_records([bitcoin.transformation_payload()])) == 1
    assert bittrex.record_type == "unknown"
    assert (
        parse_bittrex_records([bittrex.transformation_payload()])[0].external_id
        == "bittrex:order:BATCH-2"
    )
    naive = parse_bitcoin_de_statement(
        {
            "id": "NAIVE",
            "date": "2020-01-01T00:00:00",
            "type": "buy",
            "asset": "BTC",
            "quantity": "1",
            "cost_eur": "1",
        }
    )
    assert naive.occurred_at == datetime(2020, 1, 1, tzinfo=UTC)


def test_historical_entities_reject_invalid_values() -> None:
    import pytest

    with pytest.raises(ValueError):
        HistoricalTransferLink(
            stable_key="x",
            version="v1",
            evidence_type="source",
            source_record_ids=("r",),
            canonical_asset="BTC",
            source_quantity=Decimal("-1"),
            target_quantity=Decimal("1"),
            transfer_nature=HistoricalTransferNature.UNKNOWN,
            basis_coverage=HistoricalBasisCoverage.UNKNOWN,
            source_fee_quantity=Decimal("0"),
            source_fee_asset=None,
            transport_difference=Decimal("0"),
            evidence_hash="hash",
            resolution_hash="hash",
            created_at=NOW,
        )
    with pytest.raises(ValueError, match="finite"):
        HistoricalTransferLink(
            stable_key="x-nan",
            version="v1",
            evidence_type="source",
            source_record_ids=("r",),
            canonical_asset="BTC",
            source_quantity=Decimal("NaN"),
            target_quantity=Decimal("1"),
            transfer_nature=HistoricalTransferNature.UNKNOWN,
            basis_coverage=HistoricalBasisCoverage.UNKNOWN,
            source_fee_quantity=Decimal("0"),
            source_fee_asset=None,
            transport_difference=Decimal("0"),
            evidence_hash="hash",
            resolution_hash="hash",
            created_at=NOW,
        )
    assert HistoricalResolutionStatus.REVIEW_REQUIRED.value == "review_required"
    resolved = HistoricalTransferLink(
        stable_key="x2",
        version="v1",
        evidence_type="source",
        source_record_ids=("r",),
        canonical_asset="BTC",
        source_quantity=Decimal("1"),
        target_quantity=Decimal("1"),
        transfer_nature=HistoricalTransferNature.UNKNOWN,
        basis_coverage=HistoricalBasisCoverage.UNKNOWN,
        source_fee_quantity=Decimal("0"),
        source_fee_asset=None,
        transport_difference=Decimal("0"),
        evidence_hash="hash",
        resolution_hash="hash",
        created_at=NOW,
    )
    assert resolved.created_at == NOW
    with pytest.raises(ValueError):
        HistoricalTransferLink(
            stable_key="x3",
            version="v1",
            evidence_type="source",
            source_record_ids=("r",),
            canonical_asset="BTC",
            source_quantity=Decimal("1"),
            target_quantity=Decimal("-1"),
            transfer_nature=HistoricalTransferNature.UNKNOWN,
            basis_coverage=HistoricalBasisCoverage.UNKNOWN,
            source_fee_quantity=Decimal("0"),
            source_fee_asset=None,
            transport_difference=Decimal("0"),
            evidence_hash="hash",
            resolution_hash="hash",
            created_at=NOW,
        )


def test_unresolved_asset_is_a_cost_basis_gap(monkeypatch: object) -> None:
    factory = database_factory()
    row = parse_bitcoin_de_statement(
        {
            "id": "UNRESOLVED-ASSET",
            "type": "buy",
            "asset": "BTC",
            "quantity": "1",
            "cost_eur": "1",
            "date": "2020-01-01T00:00:00Z",
        }
    )
    import_rows(factory, BITCOIN_DE_SOURCE, [row])
    source_id = raw_id(factory, "bitcoin-de:statement:UNRESOLVED-ASSET")
    monkeypatch.setattr(
        "app.services.historical_transfer.resolve_asset",
        lambda _raw: AssetIdentity(
            raw_code="MYSTERY",
            canonical_code=None,
            mapping_version="test-v1",
            mapping_status=MappingStatus.UNRESOLVED,
            review_reason="unknown",
        ),
    )
    resolve(factory, (source_id,), coverage=HistoricalBasisCoverage.PARTIAL)
    with factory() as database:
        assert database.scalar(select(func.count()).select_from(AcquisitionLot)) == 0
        assert (
            database.scalar(select(func.count()).select_from(TransformationIssue)) == 1
        )


def test_import_is_idempotent_and_transfer_link_is_stable() -> None:
    factory = database_factory()
    row = parse_bitcoin_de_statement(
        {
            "id": "BUY-1",
            "date": "2020-01-01T00:00:00Z",
            "type": "buy",
            "asset": "BTC",
            "quantity": "1.00000000",
            "cost_eur": "7000.00",
            "fee_quantity": "0.0001",
            "fee_asset": "BTC",
        }
    )
    first_session, _ = import_rows(factory, BITCOIN_DE_SOURCE, [row])
    second_session, second = import_rows(factory, BITCOIN_DE_SOURCE, [row])
    assert second.reused_count == 1
    assert second.accepted_count == 0
    source_id = raw_id(factory, "bitcoin-de:statement:BUY-1")
    first = resolve(factory, (source_id,), coverage=HistoricalBasisCoverage.COMPLETE)
    first_id = first.id
    again = resolve(factory, (source_id,), coverage=HistoricalBasisCoverage.COMPLETE)
    assert again.id == first_id
    with factory() as database:
        assert database.scalar(select(func.count()).select_from(AcquisitionLot)) == 1
        assert database.scalar(select(func.count()).select_from(FeeEvent)) == 1
        assert (
            database.scalar(select(func.count()).select_from(TransformationIssue)) == 0
        )
        assert first_session != second_session


def test_changed_target_fingerprint_is_a_distinct_evidence_link() -> None:
    factory = database_factory()
    row = parse_bitcoin_de_statement(
        {
            "id": "BUY-2",
            "date": "2020-01-01T00:00:00Z",
            "type": "buy",
            "asset": "BTC",
            "quantity": "1",
            "cost_eur": "1",
        }
    )
    import_rows(factory, BITCOIN_DE_SOURCE, [row])
    source_id = raw_id(factory, "bitcoin-de:statement:BUY-2")
    service = HistoricalTransferService(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(factory), clock=lambda: NOW
    )
    service.resolve(
        source_record_ids=(source_id,),
        canonical_asset="BTC",
        source_quantity=Decimal("1"),
        target_quantity=Decimal("1"),
        transfer_nature=HistoricalTransferNature.SELF_TRANSFER,
        basis_coverage=HistoricalBasisCoverage.COMPLETE,
        evidence_type=BITCOIN_DE_SOURCE,
        target_fingerprint="target-a",
    )
    service.resolve(
        source_record_ids=(source_id,),
        canonical_asset="BTC",
        source_quantity=Decimal("1"),
        target_quantity=Decimal("1"),
        transfer_nature=HistoricalTransferNature.SELF_TRANSFER,
        basis_coverage=HistoricalBasisCoverage.COMPLETE,
        evidence_type=BITCOIN_DE_SOURCE,
        target_fingerprint="target-b",
    )
    with factory() as database:
        assert (
            database.scalar(
                select(func.count()).select_from(models.HistoricalTransferLink)
            )
            == 2
        )


def test_partial_basis_creates_gap_without_zero_cost_lot() -> None:
    factory = database_factory()
    row = parse_bitcoin_de_statement(
        {"id": "PARTIAL-1", "type": "buy", "asset": "BTC", "quantity": "0.5"}
    )
    import_rows(factory, BITCOIN_DE_SOURCE, [row])
    source_id = raw_id(factory, "bitcoin-de:statement:PARTIAL-1")
    resolution = resolve(
        factory, (source_id,), coverage=HistoricalBasisCoverage.PARTIAL
    )
    assert resolution.status.value == "partial"
    with factory() as database:
        assert database.scalar(select(func.count()).select_from(AcquisitionLot)) == 0
        issue = database.scalars(select(TransformationIssue)).one()
        assert issue.code == "historical_cost_basis_gap"
        assert "unresolved_quantity=0.5" in issue.message


def test_unknown_source_type_is_not_projected() -> None:
    factory = database_factory()
    row = parse_bitcoin_de_statement(
        {
            "id": "UNKNOWN-1",
            "type": "mystery",
            "asset": "BTC",
            "quantity": "1",
            "cost_eur": "1",
            "date": "2020-01-01T00:00:00Z",
        }
    )
    import_rows(factory, BITCOIN_DE_SOURCE, [row])
    source_id = raw_id(factory, "bitcoin-de:statement:UNKNOWN-1")
    resolve(factory, (source_id,), coverage=HistoricalBasisCoverage.COMPLETE)
    with factory() as database:
        assert database.scalar(select(func.count()).select_from(AcquisitionLot)) == 0


def test_explicit_network_fee_record_projects_only_a_fee_event() -> None:
    factory = database_factory()
    row = parse_bitcoin_de_statement(
        {
            "id": "FEE-1",
            "type": "network_fee",
            "asset": "ETH",
            "fee": "0.00191730",
            "fee_asset": "ETH",
            "date": "2020-01-01T00:00:00Z",
        }
    )
    malformed = parse_bitcoin_de_statement(
        {"id": "FEE-EMPTY", "type": "network_fee", "asset": "ETH"}
    )
    import_rows(factory, BITCOIN_DE_SOURCE, [row, malformed])
    source_ids = tuple(
        raw_id(factory, item.raw_record().external_id or "")
        for item in (row, malformed)
    )
    resolve(factory, source_ids, coverage=HistoricalBasisCoverage.COMPLETE)
    with factory() as database:
        assert database.scalar(select(func.count()).select_from(AcquisitionLot)) == 0
        fee = database.scalars(select(FeeEvent)).one()
        assert fee.quantity == Decimal("0.00191730")


def test_malformed_source_records_create_a_gap_and_never_a_zero_cost_lot() -> None:
    factory = database_factory()
    rows = [
        parse_bitcoin_de_statement(
            {
                "id": "BAD-DATE",
                "type": "buy",
                "asset": "BTC",
                "quantity": "1",
                "cost_eur": "1",
                "date": "not-a-date",
            }
        ),
        parse_bitcoin_de_statement(
            {
                "id": "BAD-COST",
                "type": "buy",
                "asset": "BTC",
                "quantity": "1",
                "cost_eur": "1",
                "date": "2020-01-01T00:00:00Z",
            }
        ),
        parse_bitcoin_de_statement(
            {
                "id": "BAD-ASSET",
                "type": "buy",
                "asset": "",
                "quantity": "1",
                "cost_eur": "1",
                "date": "2020-01-01T00:00:00Z",
            }
        ),
    ]
    inputs = [
        replace(
            row.raw_record(),
            payload={**row.transformation_payload(), "occurred_at": occurred_at},
        )
        for row, occurred_at in zip(
            rows,
            ("not-a-date", "2020-01-01T00:00:00", "2020-01-01T00:00:00"),
            strict=True,
        )
    ]
    inputs[1] = replace(inputs[1], payload={**inputs[1].payload, "cost_eur": "abc"})
    import_inputs(factory, BITCOIN_DE_SOURCE, inputs)
    ids = tuple(raw_id(factory, row.raw_record().external_id or "") for row in rows)
    service = HistoricalTransferService(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(factory), clock=lambda: NOW
    )
    with factory() as database:
        assert database.scalar(select(func.count()).select_from(AcquisitionLot)) == 0
    service.resolve(
        source_record_ids=ids,
        canonical_asset="BTC",
        source_quantity=Decimal("2"),
        target_quantity=Decimal("2"),
        transfer_nature=HistoricalTransferNature.SELF_TRANSFER,
        basis_coverage=HistoricalBasisCoverage.PARTIAL,
        evidence_type=BITCOIN_DE_SOURCE,
    )
    service.resolve(
        source_record_ids=("not-a-uuid",),
        canonical_asset="BTC",
        source_quantity=Decimal("1"),
        target_quantity=Decimal("1"),
        transfer_nature=HistoricalTransferNature.UNKNOWN,
        basis_coverage=HistoricalBasisCoverage.UNKNOWN,
        evidence_type=BITCOIN_DE_SOURCE,
    )
    with factory() as database:
        assert (
            database.scalar(select(func.count()).select_from(TransformationIssue)) == 3
        )


def test_btc_fifo_regression_uses_exact_decimal_allocations() -> None:
    quantities = [
        ("SUGP63", "0.11111405"),
        ("VX8YWX", "0.09920000"),
        ("PARTNER", "0.00001805"),
        ("5MYSY7", "0.13293441"),
        ("TAM2PF", "0.24800000"),
        ("GZZPMS", "0.23359506"),
    ]
    acquisitions = [
        AcquisitionInput(
            acquisition_id=UUID(int=index + 1),
            asset_code="BTC",
            quantity=Decimal(value),
            acquired_at=datetime(2018 + index // 2, 1, 1, tzinfo=UTC),
            value_eur=Decimal("1"),
            fee_eur=Decimal("0"),
            valuation_decision_id=UUID(int=100 + index),
            acquisition_type="historical_external",
        )
        for index, (_name, value) in enumerate(quantities)
    ]
    disposal = DisposalInput(
        disposal_id=UUID(int=1000),
        asset_code="BTC",
        quantity=Decimal("0.5"),
        disposed_at=datetime(2021, 6, 4, tzinfo=UTC),
        proceeds_eur=Decimal("1"),
        fee_eur=Decimal("0"),
        valuation_decision_id=UUID(int=2000),
        disposal_type="sale",
    )
    fee = DisposalInput(
        disposal_id=UUID(int=1001),
        asset_code="BTC",
        quantity=Decimal("0.00007986"),
        disposed_at=datetime(2021, 6, 4, 0, 1, tzinfo=UTC),
        proceeds_eur=Decimal("0.000001"),
        fee_eur=Decimal("0"),
        valuation_decision_id=UUID(int=2001),
        disposal_type="crypto_fee",
    )
    result = calculate_fifo(
        run_id=UUID(int=3000),
        period=TaxReportingPeriod.for_year(2021),
        rules=TaxRuleVersion(),
        acquisitions=acquisitions,
        disposals=[disposal, fee],
    )
    first = [allocation.allocated_quantity for allocation in result.allocations[:5]]
    assert first == [Decimal(value) for _name, value in quantities[:4]] + [
        Decimal("0.15673349")
    ]
    assert result.allocations[5].allocated_quantity == Decimal("0.00007986")
    remaining = [lot.remaining_quantity for lot in result.lots]
    assert remaining[-2:] == [Decimal("0.09118665"), Decimal("0.23359506")]


def test_real_export_parsers_normalize_german_and_bittrex_rows(tmp_path) -> None:
    bitcoin_path = tmp_path / "bitcoin.csv"
    bitcoin_path.write_text(
        "Datum;Typ;Währung;Referenz;Menge nach Bitcoin.de-Gebühr;"
        "Einheit (Menge nach Bitcoin.de-Gebühr);Zu- / Abgang\n"
        '"2020-12-22 18:15:19";Netzwerk-Gebühr;ETH;TX; ; ;-0.00191730\n'
        '"2020-08-10 09:46:02";Kauf;ETH;EPX8XN;988.94;EUR;2.92050000\n'
        '"2020-08-11T09:46:02Z";Korrekturposition;ETH;;;;0.00000001\n',
        encoding="utf-8",
    )
    bittrex_path = tmp_path / "bittrex.csv"
    bittrex_path.write_text(
        "Uuid,Exchange,TimeStamp,OrderType,Quantity,Price,PricePerUnit\n"
        "ORDER-1,BTC-DOGE,2/10/2018 10:44:26 AM,LIMIT_BUY,841.39125000,"
        "0.00050483,0.00000060\n"
        "ORDER-2,BTC-DOGE,,UNKNOWN,1,0,0\n"
        "ORDER-3,BTC-DOGE,2/10/2018 10:44:26 AM,LIMIT_SELL,1,0,0\n",
        encoding="utf-8",
    )
    bitcoin = parse_bitcoin_de_csv(bitcoin_path)
    bittrex = parse_bittrex_csv(bittrex_path)
    assert bitcoin[0].payload["record_type"] == "network_fee"
    assert bitcoin[0].payload["fee_quantity"] == "0.00191730"
    assert bitcoin[1].payload["record_type"] == "buy"
    assert bitcoin[1].payload["cost_eur"] == "988.94"
    assert bitcoin[2].payload["record_type"] == "correction"
    assert bittrex[0].payload["record_type"] == "buy"
    assert bittrex[0].payload["asset"] == "DOGE"
    assert bittrex[1].payload["occurred_at"] == ""
    assert bittrex[2].payload["record_type"] == "sell"


def test_manual_transfer_evidence_is_date_precise_and_deterministic(tmp_path) -> None:
    path = tmp_path / "manual.csv"
    path.write_text(
        "source_document;source_document_sha256;evidence_type;transaction;asset;"
        "amount;fee;fee_asset;date\n"
        "ledger.ods;abc;manual_bookkeeping;Transfer;DOGE;11402.55352573;2;"
        "DOGE;2020-12-22\n",
        encoding="utf-8",
    )
    first = parse_manual_transfer_evidence(path)
    second = parse_manual_transfer_evidence(path)
    assert first == second
    assert first[0].payload["record_type"] == "manual_transfer_fee"
    assert first[0].payload["occurred_at_precision"] == "date"
    assert first[0].technical_metadata["evidence_level"] == "manual_bookkeeping"
    assert first[0].canonical_key.startswith(f"{MANUAL_BOOKKEEPING_SOURCE}:transfer:")


def test_manual_transfer_evidence_rejects_non_manual_rows(tmp_path) -> None:
    path = tmp_path / "invalid-manual.csv"
    path.write_text(
        "source_document;source_document_sha256;evidence_type;transaction;asset;"
        "amount;fee;fee_asset;date\n"
        "ledger.ods;abc;original_bittrex_export;Transfer;DOGE;1;2;DOGE;"
        "2020-12-22\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Invalid manual transfer evidence"):
        parse_manual_transfer_evidence(path)


def test_manual_transfer_evidence_rejects_invalid_date(tmp_path) -> None:
    path = tmp_path / "invalid-date.csv"
    path.write_text(
        "source_document;source_document_sha256;evidence_type;transaction;asset;"
        "amount;fee;fee_asset;date\n"
        "ledger.ods;abc;manual_bookkeeping;Transfer;DOGE;1;2;DOGE;not-a-date\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Invalid manual transfer evidence"):
        parse_manual_transfer_evidence(path)


def test_known_date_without_eur_cost_requires_valuation_not_gap() -> None:
    factory = database_factory()
    row = parse_bitcoin_de_statement(
        {
            "id": "KNOWN-DATE-NO-EUR",
            "type": "partner_program",
            "asset": "BTC",
            "quantity": "0.00001805",
            "date": "2018-02-01T00:00:00Z",
        }
    )
    import_rows(factory, BITCOIN_DE_SOURCE, [row])
    source_id = raw_id(factory, "bitcoin-de:statement:KNOWN-DATE-NO-EUR")
    HistoricalTransferService(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(factory), clock=lambda: NOW
    ).resolve(
        source_record_ids=(source_id,),
        canonical_asset="BTC",
        source_quantity=Decimal("0.00001805"),
        target_quantity=Decimal("0.00001805"),
        transfer_nature=HistoricalTransferNature.SELF_TRANSFER,
        basis_coverage=HistoricalBasisCoverage.COMPLETE,
        evidence_type=BITCOIN_DE_SOURCE,
    )
    with factory() as database:
        lot = database.scalars(select(AcquisitionLot)).one()
        requirement = database.scalars(select(ValuationRequirement)).one()
        assert lot.quantity == Decimal("0.00001805")
        assert lot.valuation_status.value == "valuation_required"
        assert requirement.reason_code == "historical_external_acquisition"
        assert (
            database.scalar(select(func.count()).select_from(TransformationIssue)) == 0
        )


def test_direct_historical_fee_projects_one_valuation_requirement_idempotently() -> (
    None
):
    factory = database_factory()
    rows = [
        RawRecordInput(
            payload={
                "record_type": "network_fee",
                "occurred_at": "2020-01-01T00:00:00+00:00",
                "asset": "BCH",
                "fee_quantity": "0.00000400",
                "fee_asset": "BCH",
            },
            external_id="bitcoin-de:statement:FEE-1",
            canonical_key="bitcoin-de:statement:FEE-1",
        ),
        RawRecordInput(
            payload={
                "record_type": "network_fee",
                "occurred_at": "2021-01-01T00:00:00+00:00",
                "asset": "BCH",
                "fee_quantity": "0.00000400",
                "fee_asset": "BCH",
            },
            external_id="bitcoin-de:statement:FEE-2",
            canonical_key="bitcoin-de:statement:FEE-2",
        ),
    ]
    source_session, _ = import_inputs(factory, BITCOIN_DE_SOURCE, rows)
    source_ids = tuple(
        raw_id(factory, f"bitcoin-de:statement:FEE-{index}") for index in (1, 2)
    )
    service = HistoricalTransferService(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(factory), clock=lambda: NOW
    )
    for source_id in source_ids:
        service.resolve(
            source_record_ids=(source_id,),
            canonical_asset="BCH",
            source_quantity=Decimal("1"),
            target_quantity=Decimal("1"),
            transfer_nature=HistoricalTransferNature.SELF_TRANSFER,
            basis_coverage=HistoricalBasisCoverage.COMPLETE,
            evidence_type=BITCOIN_DE_SOURCE,
        )
    service.resolve(
        source_record_ids=(source_ids[0],),
        canonical_asset="BCH",
        source_quantity=Decimal("1"),
        target_quantity=Decimal("1"),
        transfer_nature=HistoricalTransferNature.SELF_TRANSFER,
        basis_coverage=HistoricalBasisCoverage.COMPLETE,
        evidence_type=BITCOIN_DE_SOURCE,
    )
    with factory() as database:
        assert database.scalar(select(func.count()).select_from(FeeEvent)) == 2
        fee_events = list(database.scalars(select(FeeEvent)))
        provenance = list(database.scalars(select(models.DomainProvenance)))
        raw_by_fee = {
            item.domain_object_id: database.get(
                models.RawImportRecord, item.raw_import_record_id
            )
            for item in provenance
            if item.domain_object_type == "FeeEvent"
        }
        assert {raw.external_id for raw in raw_by_fee.values() if raw is not None} == {
            "bitcoin-de:statement:FEE-1",
            "bitcoin-de:statement:FEE-2",
        }
        assert {event.occurred_at for event in fee_events} == {
            datetime(2020, 1, 1, tzinfo=UTC),
            datetime(2021, 1, 1, tzinfo=UTC),
        }
        requirements = list(database.scalars(select(ValuationRequirement)))
        assert len(requirements) == 2
        assert all(
            item.reason_code == "historical_external_fee" for item in requirements
        )
        assert {item.domain_object_id for item in requirements} == {
            item.id for item in database.scalars(select(FeeEvent))
        }
    del source_session


def test_resolved_target_deposit_is_internal_without_new_kraken_lot() -> None:
    factory = database_factory()
    source = parse_bitcoin_de_statement(
        {
            "id": "SOURCE-BUY",
            "type": "buy",
            "asset": "BTC",
            "quantity": "1",
            "cost_eur": "1000",
            "date": "2018-01-01T00:00:00Z",
        }
    )
    source_session, _ = import_rows(factory, BITCOIN_DE_SOURCE, [source])
    fee_source = parse_bitcoin_de_statement(
        {
            "id": "SOURCE-FEE",
            "type": "network_fee",
            "asset": "BTC",
            "fee": "0.01",
            "fee_asset": "BTC",
            "date": "2020-12-31T00:00:00Z",
        }
    )
    fee_session, _ = import_rows(factory, BITCOIN_DE_SOURCE, [fee_source])
    target = RawRecordInput(
        payload={
            "txid": "TARGET-DEPOSIT",
            "refid": "TARGET-REF",
            "time": "2021-01-01 00:00:00",
            "type": "deposit",
            "subtype": "",
            "asset": "XXBT",
            "amount": "1",
            "fee": "0",
        },
        external_id="kraken:ledger:TARGET-DEPOSIT",
        canonical_key="kraken:ledger:TARGET-DEPOSIT",
        technical_metadata={
            "canonical_asset": {
                "raw_asset": "XXBT",
                "normalized_asset": "BTC",
                "is_unambiguous": True,
            },
            "asset_mapping_version": "kraken-assets-v2",
        },
    )
    target_session, _ = import_inputs(factory, "kraken-ledgers", [target])
    source_id = raw_id(factory, "bitcoin-de:statement:SOURCE-BUY")
    fee_source_id = raw_id(factory, "bitcoin-de:statement:SOURCE-FEE")
    target_id = raw_id(factory, "kraken:ledger:TARGET-DEPOSIT")
    with factory() as database:
        target_hash = database.get(models.RawImportRecord, target_id).content_hash
    HistoricalTransferService(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(factory), clock=lambda: NOW
    ).resolve(
        source_record_ids=(source_id,),
        canonical_asset="BTC",
        source_quantity=Decimal("1"),
        target_quantity=Decimal("1"),
        transfer_nature=HistoricalTransferNature.SELF_TRANSFER,
        basis_coverage=HistoricalBasisCoverage.COMPLETE,
        evidence_type=BITCOIN_DE_SOURCE,
        target_raw_import_record_id=target_id,
        target_fingerprint=target_hash,
        source_fee_quantity=Decimal("0.01"),
        source_fee_asset="BTC",
        fee_source_record_id=fee_source_id,
    )
    result = KrakenTransformationService(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(factory), clock=lambda: NOW
    ).transform(
        import_session_ids=(target_session,),
        context_import_session_ids=(source_session, fee_session),
        actor_id="test-suite",
    )
    assert result.review_cases == 0
    with factory() as database:
        decisions = database.scalars(select(TransformationDecision)).all()
        assert decisions[-1].reason_code == "ledger_historical_self_transfer_resolved"
        assert database.scalar(select(func.count()).select_from(AcquisitionLot)) == 1
        assert database.scalar(select(func.count()).select_from(FeeEvent)) == 1
        fee_event = database.scalars(select(FeeEvent)).one()
        fee_provenance = database.scalars(
            select(models.DomainProvenance).where(
                models.DomainProvenance.domain_object_type == "FeeEvent",
                models.DomainProvenance.domain_object_id == fee_event.id,
            )
        ).one()
        fee_raw = database.get(
            models.RawImportRecord, fee_provenance.raw_import_record_id
        )
        assert fee_raw is not None
        assert fee_raw.external_id == "bitcoin-de:statement:SOURCE-FEE"
        assert fee_event.occurred_at == datetime(2020, 12, 31, tzinfo=UTC)
        assert (
            database.scalar(select(func.count()).select_from(ValuationRequirement)) == 1
        )


def test_historical_resolution_rejects_target_mismatch_and_bad_quantities() -> None:
    factory = database_factory()
    with pytest.raises(ValueError, match="source_quantity"):
        HistoricalTransferService(
            unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(factory),
            clock=lambda: NOW,
        ).resolve(
            source_record_ids=(),
            canonical_asset="BTC",
            source_quantity=Decimal("0.1"),
            target_quantity=Decimal("0.2"),
            transfer_nature=HistoricalTransferNature.SELF_TRANSFER,
            basis_coverage=HistoricalBasisCoverage.COMPLETE,
            evidence_type=BITCOIN_DE_SOURCE,
        )


def test_historical_target_match_is_fail_closed() -> None:
    factory = database_factory()
    source_session, _ = import_rows(
        factory,
        BITCOIN_DE_SOURCE,
        [
            parse_bitcoin_de_statement(
                {
                    "id": "TARGET-SOURCE",
                    "type": "buy",
                    "asset": "BTC",
                    "quantity": "1",
                    "cost_eur": "1000",
                    "date": "2018-01-01T00:00:00Z",
                }
            )
        ],
    )
    target_session, _ = import_inputs(
        factory,
        "kraken-ledgers",
        [
            RawRecordInput(
                payload={
                    "txid": "TARGET-MATCH",
                    "refid": "TARGET-MATCH",
                    "time": "2021-01-01 00:00:00",
                    "type": "deposit",
                    "subtype": "",
                    "asset": "XXBT",
                    "amount": "1",
                    "fee": "0",
                },
                external_id="kraken:ledger:TARGET-MATCH",
                canonical_key="kraken:ledger:TARGET-MATCH",
                technical_metadata={
                    "canonical_asset": {
                        "raw_asset": "XXBT",
                        "normalized_asset": "BTC",
                        "is_unambiguous": True,
                    },
                    "asset_mapping_version": "kraken-assets-v2",
                },
            )
        ],
    )
    source_id = raw_id(factory, "bitcoin-de:statement:TARGET-SOURCE")
    target_id = raw_id(factory, "kraken:ledger:TARGET-MATCH")
    with factory() as database:
        record = database.get(models.RawImportRecord, target_id)
        assert record is not None
        target_hash = record.content_hash
    HistoricalTransferService(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(factory), clock=lambda: NOW
    ).resolve(
        source_record_ids=(source_id,),
        canonical_asset="BTC",
        source_quantity=Decimal("1"),
        target_quantity=Decimal("1"),
        transfer_nature=HistoricalTransferNature.SELF_TRANSFER,
        basis_coverage=HistoricalBasisCoverage.COMPLETE,
        evidence_type=BITCOIN_DE_SOURCE,
        target_raw_import_record_id=target_id,
        target_fingerprint=target_hash,
    )
    with factory() as database:
        link = database.scalars(select(HistoricalTransferLink)).one()
        raw = database.get(models.RawImportRecord, target_id)
        assert raw is not None
        assert kraken_transformation._historical_target_matches(link, raw)
        assert not kraken_transformation._historical_target_matches(link, None)
        assert not kraken_transformation._historical_target_matches(
            replace(link, target_fingerprint="wrong"), raw
        )
        assert not kraken_transformation._historical_target_matches(
            link, replace(raw, payload={**raw.payload, "type": "withdrawal"})
        )
        assert not kraken_transformation._historical_target_matches(
            link, replace(raw, payload={**raw.payload, "amount": "not-a-number"})
        )
        assert not kraken_transformation._historical_target_matches(
            link, replace(raw, payload={**raw.payload, "amount": "2"})
        )
        with SqlAlchemyUnitOfWork(factory) as unit:
            assert (
                kraken_transformation._historical_resolution_for_link(
                    unit, replace(link, id=new_id())
                )
                is None
            )
        database.execute(
            models.RawImportRecord.__table__.update()
            .where(models.RawImportRecord.id == target_id)
            .values(content_hash="changed-after-resolution")
        )
        database.commit()
    result = KrakenTransformationService(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(factory), clock=lambda: NOW
    ).transform(
        import_session_ids=(target_session,),
        context_import_session_ids=(source_session,),
        actor_id="test-suite",
    )
    assert result.review_cases == 1


def test_cost_gap_with_unparseable_source_id_fails_closed() -> None:
    factory = database_factory()
    link = HistoricalTransferLink(
        stable_key="gap-test",
        version="v1",
        evidence_type=BITCOIN_DE_SOURCE,
        source_record_ids=("not-a-uuid",),
        canonical_asset="BTC",
        source_quantity=Decimal("1"),
        target_quantity=Decimal("1"),
        transfer_nature=HistoricalTransferNature.SELF_TRANSFER,
        basis_coverage=HistoricalBasisCoverage.PARTIAL,
        source_fee_quantity=Decimal("0"),
        source_fee_asset=None,
        transport_difference=Decimal("0"),
        evidence_hash="evidence",
        resolution_hash="resolution",
    )
    run = TransformationRun(
        contract_version="test",
        status=TransformationStatus.COMPLETED,
        started_at=NOW,
        actor_id="test-suite",
    )
    with SqlAlchemyUnitOfWork(factory) as unit:
        unit.transformation_runs.add(run)
        HistoricalTransferService._gap(unit, run, None, link, Decimal("0.1"), NOW)
        unit.commit()
    assert historical_transfer_service._raw_uuid(UUID(int=1)) == UUID(int=1)
    assert historical_transfer_service._raw_uuid("not-a-uuid") is None
