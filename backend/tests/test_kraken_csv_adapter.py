from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app import models
from app.adapters.kraken.models import (
    KrakenExportKind,
    KrakenLedgerRow,
    KrakenTradeRow,
)
from app.adapters.kraken.parser import KrakenCsvParser
from app.adapters.kraken.service import KrakenCsvImportService
from app.core.entities import (
    AuditActorType,
    AuditEvent,
    ImportError,
    ImportSession,
    RawImportRecord,
)
from app.core.identifiers import Uuid4IdGenerator
from app.database.base import Base
from app.database.unit_of_work import SqlAlchemyUnitOfWork
from app.imports.service import ImportOutcome, ImportService
from app.imports.validation import RequiredFieldsValidator

NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)
LEDGER_HEADER = "txid,time,type,asset,amount,fee"
TRADE_HEADER = "txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol"


def ledger_row(
    *,
    txid: str = "L1",
    time: str = "2026-01-02 03:04:05.0000",
    kind: str = "trade",
    asset: str = "XXBT",
    amount: str = "1.2300",
    fee: str = "0",
) -> str:
    return ",".join((txid, time, kind, asset, amount, fee))


def trade_row(
    *,
    txid: str = "T1",
    ordertxid: str = "O1",
    side: str = "buy",
    ordertype: str = "limit",
    price: str = "100.00",
    cost: str = "200.00",
    fee: str = "0.20",
    vol: str = "2.0",
) -> str:
    return ",".join(
        (
            txid,
            ordertxid,
            "XXBTZEUR",
            "2026-01-02 03:04:05",
            side,
            ordertype,
            price,
            cost,
            fee,
            vol,
        )
    )


def parse(csv_text: str | bytes):
    return KrakenCsvParser().parse(csv_text)


def database_factory() -> sessionmaker[Session]:
    models.configure_mappings()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def adapter(factory: sessionmaker[Session]) -> KrakenCsvImportService:
    identifiers = Uuid4IdGenerator()
    generic = ImportService(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(factory),
        id_generator=identifiers,
        validator=RequiredFieldsValidator(),
        clock=lambda: NOW,
    )
    return KrakenCsvImportService(
        import_service=generic,
        id_generator=identifiers,
        clock=lambda: NOW,
    )


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (f"{LEDGER_HEADER}\n{ledger_row()}\n", KrakenExportKind.LEDGERS),
        (f"{TRADE_HEADER}\n{trade_row()}\n", KrakenExportKind.TRADES),
        (
            "\ufeff TXID , TIME , TYPE , ASSET , AMOUNT , FEE \r\n"
            + ledger_row()
            + "\r\n",
            KrakenExportKind.LEDGERS,
        ),
    ],
)
def test_detects_supported_headers_and_common_encodings(
    text: str, kind: KrakenExportKind
) -> None:
    batch, errors = parse(text.encode())
    assert errors == ()
    assert batch is not None and batch.export_kind is kind


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("", "kraken_csv_empty"),
        (LEDGER_HEADER, "kraken_csv_header_only"),
        (b"\xff", "kraken_csv_invalid_utf8"),
        (
            "txid;time;type;asset;amount;fee\nx;y;z;a;1;0",
            "kraken_csv_unsupported_delimiter",
        ),
        ("one,two\nx,y", "kraken_csv_unknown_export_kind"),
        (
            "txid,time,type,asset,amount\nx,t,t,a,1",
            "kraken_csv_missing_required_columns",
        ),
        (
            "txid,time,type,asset,amount,fee,pair,ordertxid,ordertype,price,cost,vol\n"
            "x,t,t,a,1,0,p,o,o,1,1,1",
            "kraken_csv_mixed_export_kind",
        ),
        ("txid,asset,pair\nx,a,p", "kraken_csv_mixed_export_kind"),
        (
            "txid, TXID,time,type,asset,amount,fee\nx,x,t,t,a,1,0",
            "kraken_csv_duplicate_header",
        ),
        (f"{LEDGER_HEADER}\n{ledger_row()},extra", "kraken_csv_malformed"),
        (f'{LEDGER_HEADER}\n"unterminated', "kraken_csv_malformed"),
    ],
)
def test_reports_csv_structure_errors(text: str | bytes, code: str) -> None:
    batch, errors = parse(text)
    assert batch is None
    assert errors[0].code == code


def test_rejects_excessive_column_count() -> None:
    headers = ",".join(f"column{number}" for number in range(129))
    batch, errors = parse(headers)
    assert batch is None
    assert errors[0].code == "kraken_csv_malformed"


@pytest.mark.parametrize(
    ("kind", "subtype"),
    [
        ("earn", "reward"),
        ("earn", "allocation"),
        ("earn", "deallocation"),
        ("staking", ""),
        ("future ledger type", "future subtype"),
    ],
)
def test_ledger_preserves_types_subtypes_and_unknown_fields(
    kind: str, subtype: str
) -> None:
    header = LEDGER_HEADER + ",subtype,aclass,balance,future"
    text = (
        f'{header}\n{ledger_row(kind=kind, amount="-1.2300")},'
        f'{subtype},currency,9.5,"a,b"\n'
    )
    batch, errors = parse(text)
    assert errors == ()
    assert batch is not None
    row = batch.rows[0]
    assert isinstance(row, KrakenLedgerRow)
    assert row.amount == Decimal("-1.2300")
    assert row.fee == Decimal("0")
    assert row.balance == Decimal("9.5")
    assert row.normalized_values["type"] == kind
    assert row.normalized_values["subtype"] == subtype
    assert row.extra_fields == {"future": "a,b"}
    assert row.original_values["asset"] == "XXBT"
    assert row.occurred_at.tzinfo is UTC


def test_ledger_optional_columns_may_be_absent_and_multiline_values_survive() -> None:
    text = (
        LEDGER_HEADER
        + ",note\n"
        + ledger_row(amount="+2.00")
        + ',"line one\nline two"\n'
    )
    batch, errors = parse(text)
    assert errors == ()
    assert batch is not None
    row = batch.rows[0]
    assert isinstance(row, KrakenLedgerRow)
    assert row.balance is None
    assert row.extra_fields["note"] == "line one\nline two"
    assert row.source_line == 3


@pytest.mark.parametrize("field", ["amount", "fee", "balance"])
def test_ledger_rejects_invalid_decimals(field: str) -> None:
    header = LEDGER_HEADER + (",balance" if field == "balance" else "")
    values = {"amount": "1", "fee": "0", "balance": "2"}
    values[field] = "1,2"
    row = ledger_row(
        amount=f'"{values["amount"]}"',
        fee=f'"{values["fee"]}"',
    )
    if field == "balance":
        row += f',"{values["balance"]}"'
    batch, errors = parse(f"{header}\n{row}\n")
    assert batch is None
    assert any(
        error.code == "kraken_csv_invalid_decimal" and error.field == field
        for error in errors
    )


@pytest.mark.parametrize("timestamp", ["bad", "2026-13-01 00:00:00"])
def test_ledger_rejects_invalid_timestamp(timestamp: str) -> None:
    batch, errors = parse(f"{LEDGER_HEADER}\n{ledger_row(time=timestamp)}\n")
    assert batch is None
    assert errors[0].code == "kraken_csv_invalid_timestamp"
    assert errors[0].record_position == 2


def test_ledger_reports_duplicate_txid_and_empty_required_fields() -> None:
    text = f"{LEDGER_HEADER}\n{ledger_row()}\n{ledger_row()}\n,,trade,XXBT,1,0\n"
    batch, errors = parse(text)
    assert batch is None
    codes = {error.code for error in errors}
    assert "kraken_csv_duplicate_txid" in codes
    assert "kraken_csv_empty_required_value" in codes


@pytest.mark.parametrize(
    ("side", "ordertype"), [("buy", "limit"), ("sell", "market"), ("buy", "future")]
)
def test_trade_supports_sides_and_forward_compatible_order_types(
    side: str, ordertype: str
) -> None:
    header = TRADE_HEADER + ",margin,misc,ledgers,future"
    row = trade_row(side=side, ordertype=ordertype)
    batch, errors = parse(f'{header}\n{row},,tag,"L1,L2",kept\n')
    assert errors == ()
    assert batch is not None
    parsed = batch.rows[0]
    assert isinstance(parsed, KrakenTradeRow)
    assert parsed.price == Decimal("100.00")
    assert parsed.cost == Decimal("200.00")
    assert parsed.fee == Decimal("0.20")
    assert parsed.volume == Decimal("2.0")
    assert parsed.margin is None
    assert parsed.ledger_references == ("L1", "L2")
    assert parsed.normalized_values["ledgers"] == "L1,L2"
    assert parsed.extra_fields == {"future": "kept"}


@pytest.mark.parametrize(("ledger_value", "expected"), [("", ()), ("L1", ("L1",))])
def test_trade_ledger_reference_forms(
    ledger_value: str, expected: tuple[str, ...]
) -> None:
    batch, errors = parse(f"{TRADE_HEADER},ledgers\n{trade_row()},{ledger_value}\n")
    assert errors == ()
    assert batch is not None
    row = batch.rows[0]
    assert isinstance(row, KrakenTradeRow)
    assert row.ledger_references == expected


def test_trade_allows_partial_fills_with_same_order_id_but_unique_txids() -> None:
    text = f"{TRADE_HEADER}\n{trade_row(txid='T1')}\n{trade_row(txid='T2')}\n"
    batch, errors = parse(text)
    assert errors == ()
    assert batch is not None and len(batch.rows) == 2
    duplicate, duplicate_errors = parse(
        f"{TRADE_HEADER}\n{trade_row()}\n{trade_row()}\n"
    )
    assert duplicate is None
    assert duplicate_errors[0].code == "kraken_csv_duplicate_txid"


@pytest.mark.parametrize("field", ["price", "cost", "fee", "vol", "margin"])
def test_trade_rejects_invalid_decimals(field: str) -> None:
    values = {"price": "1", "cost": "2", "fee": "0", "vol": "2"}
    header = TRADE_HEADER + (",margin" if field == "margin" else "")
    if field != "margin":
        values[field] = "1e2"
    row = trade_row(**values)
    if field == "margin":
        row += ",1.2.3"
    batch, errors = parse(f"{header}\n{row}\n")
    assert batch is None
    assert any(error.field == field for error in errors)


def test_trade_rejects_bad_time_and_preserves_stable_order() -> None:
    bad = trade_row().replace("2026-01-02 03:04:05", "bad")
    batch, errors = parse(f"{TRADE_HEADER}\n{bad}\n")
    assert batch is None and errors[0].code == "kraken_csv_invalid_timestamp"
    good, good_errors = parse(
        f"{TRADE_HEADER}\n{trade_row(txid='T2')}\n{trade_row(txid='T1')}\n"
    )
    assert good_errors == ()
    assert good is not None
    assert [row.txid for row in good.rows] == ["T2", "T1"]


def test_adapter_imports_atomically_and_is_filename_bom_newline_independent() -> None:
    factory = database_factory()
    service = adapter(factory)
    content = f"{LEDGER_HEADER}\n{ledger_row()}\n"
    first = service.import_csv(
        raw_data=content,
        actor_type=AuditActorType.SYSTEM,
        actor_id="test-suite",
        source_name="first.csv",
    )
    second = service.import_csv(
        raw_data=("\ufeff" + content.replace("\n", "\r\n")).encode(),
        actor_type=AuditActorType.SYSTEM,
        actor_id="test-suite",
        source_name="renamed.csv",
    )
    assert first.import_result is not None
    assert first.import_result.outcome is ImportOutcome.SUCCESS
    assert second.import_result is not None
    assert second.import_result.outcome is ImportOutcome.DUPLICATE
    assert first.import_result.import_hash == second.import_result.import_hash
    with factory() as database:
        records = tuple(database.scalars(select(RawImportRecord)))
        assert len(records) == 1
        assert records[0].external_id == "kraken:ledger:L1"
        assert records[0].sequence_number == 0
        assert records[0].payload["asset"] == "XXBT"
        assert records[0].technical_metadata["source_line"] == 2
        assert database.scalar(select(func.count()).select_from(AuditEvent)) == 6


def test_ledger_and_trade_use_separate_sources_and_external_ids() -> None:
    factory = database_factory()
    service = adapter(factory)
    ledger = service.import_csv(
        raw_data=f"{LEDGER_HEADER}\n{ledger_row(txid='SAME')}\n",
        actor_type=AuditActorType.USER,
        actor_id="synthetic-user",
    )
    trade = service.import_csv(
        raw_data=f"{TRADE_HEADER}\n{trade_row(txid='SAME')}\n",
        actor_type=AuditActorType.USER,
        actor_id="synthetic-user",
    )
    assert ledger.successful and trade.successful
    with factory() as database:
        sessions = tuple(database.scalars(select(ImportSession)))
        records = tuple(database.scalars(select(RawImportRecord)))
        assert {session.source for session in sessions} == {
            "kraken-ledgers",
            "kraken-trades",
        }
        assert {record.external_id for record in records} == {
            "kraken:ledger:SAME",
            "kraken:trade:SAME",
        }


def test_invalid_csv_never_reaches_generic_persistence() -> None:
    factory = database_factory()
    result = adapter(factory).import_csv(
        raw_data=f"{LEDGER_HEADER}\n{ledger_row(amount='bad')}\n",
        actor_type=AuditActorType.SYSTEM,
        actor_id="test-suite",
    )
    assert not result.successful
    assert result.import_result is None
    with factory() as database:
        assert database.scalar(select(func.count()).select_from(RawImportRecord)) == 0
        assert database.scalar(select(func.count()).select_from(ImportSession)) == 0
        assert database.scalar(select(func.count()).select_from(ImportError)) == 0
