from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app import models
from app.adapters.historical_sources import (
    BITCOIN_DE_SOURCE,
    BITTREX_SOURCE,
    BitcoinDeStatementRecord,
    BittrexOrderHistoryRecord,
    parse_bitcoin_de_records,
    parse_bitcoin_de_statement,
    parse_bittrex_order_history,
    parse_bittrex_records,
)
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
    TransformationIssue,
)
from app.database.base import Base
from app.database.unit_of_work import SqlAlchemyUnitOfWork
from app.imports.context import ImportContext
from app.imports.service import ImportService, RawRecordInput
from app.imports.validation import RequiredFieldsValidator
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
