"""Deterministic adapters for external historical exchange exports.

These adapters only canonicalize source rows into RawRecordInput objects. They
do not assign tax treatment or create domain projections.
"""

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.imports.hashing import canonical_sha256
from app.imports.service import RawRecordInput

BITCOIN_DE_SOURCE = "bitcoin-de-account-statement"
BITTREX_SOURCE = "bittrex-order-history"
HISTORICAL_SOURCE_VERSION = "historical-source-v1"


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _decimal(value: Any) -> Decimal | None:
    if value in (None, "") or isinstance(value, (float, bool)):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _timestamp(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _source_timestamp(value: Any) -> datetime | None:
    """Parse the naive timestamps used by the historical exports as UTC.

    The exports do not carry an offset.  The normalized timestamp is explicit
    and timezone-aware at the application boundary; no local machine timezone
    is consulted.
    """

    text = _text(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%m/%d/%Y %I:%M:%S %p"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return _timestamp(text)


def _first(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in raw:
            return raw[key]
    return None


def _bitcoin_de_type(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    return {
        "buy": "buy",
        "purchase": "buy",
        "kauf": "buy",
        "trade_buy": "buy",
        "sell": "sell",
        "trade_sell": "sell",
        "verkauf": "sell",
        "withdrawal": "withdrawal",
        "payout": "withdrawal",
        "auszahlung": "withdrawal",
        "network_fee": "network_fee",
        "networkfee": "network_fee",
        "mining_fee": "network_fee",
        "netzwerk_gebühr": "network_fee",
        "netzwerkgebühr": "network_fee",
        "partner_program": "partner_program",
        "affiliate": "partner_program",
        "partnerprogramm": "partner_program",
        "correction": "correction",
        "adjustment": "correction",
        "korrekturposition": "correction",
    }.get(normalized, "unknown")


def _bittrex_type(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized.endswith("_buy"):
        return "buy"
    if normalized.endswith("_sell"):
        return "sell"
    return {
        "buy": "buy",
        "sell": "sell",
        "withdrawal": "withdrawal",
        "withdraw": "withdrawal",
    }.get(normalized, "unknown")


@dataclass(frozen=True, kw_only=True)
class BitcoinDeStatementRecord:
    source_id: str
    occurred_at: datetime | None
    record_type: str
    asset: str
    quantity: Decimal | None
    cost_eur: Decimal | None
    fee_quantity: Decimal | None
    fee_asset: str | None
    reference: str | None
    raw_type: str

    @property
    def canonical_key(self) -> str:
        return f"bitcoin-de:statement:{self.source_id}"

    def transformation_payload(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "occurred_at": self.occurred_at.isoformat() if self.occurred_at else "",
            "record_type": self.record_type,
            "asset": self.asset,
            "quantity": str(self.quantity) if self.quantity is not None else "",
            "cost_eur": str(self.cost_eur) if self.cost_eur is not None else "",
            "fee_quantity": (
                str(self.fee_quantity) if self.fee_quantity is not None else ""
            ),
            "fee_asset": self.fee_asset or "",
            "reference": self.reference or "",
            "raw_type": self.raw_type,
        }

    def raw_record(self) -> RawRecordInput:
        return RawRecordInput(
            payload=self.transformation_payload(),
            external_id=f"bitcoin-de:statement:{self.source_id}",
            canonical_key=self.canonical_key,
            technical_metadata={
                "source_kind": BITCOIN_DE_SOURCE,
                "normalization_version": HISTORICAL_SOURCE_VERSION,
                "evidence_level": "original_exchange",
                "canonical_fingerprint": canonical_sha256(
                    self.transformation_payload()
                ),
            },
        )


def parse_bitcoin_de_statement(raw: dict[str, Any]) -> BitcoinDeStatementRecord:
    source_id = _text(_first(raw, "source_id", "id", "reference", "trade_id"))
    if not source_id:
        raise ValueError("Bitcoin.de statement row requires a stable source_id")
    raw_type = _text(_first(raw, "record_type", "type", "transaction_type"))
    return BitcoinDeStatementRecord(
        source_id=source_id,
        occurred_at=_timestamp(_first(raw, "occurred_at", "timestamp", "date", "time")),
        record_type=_bitcoin_de_type(raw_type),
        asset=_text(_first(raw, "asset", "currency", "coin")).upper(),
        quantity=_decimal(_first(raw, "quantity", "amount", "volume")),
        cost_eur=_decimal(_first(raw, "cost_eur", "cost", "total_eur")),
        fee_quantity=_decimal(_first(raw, "fee_quantity", "fee", "network_fee")),
        fee_asset=(_text(_first(raw, "fee_asset", "fee_currency")) or None),
        reference=_text(_first(raw, "reference", "refid", "trade_id")) or None,
        raw_type=raw_type,
    )


@dataclass(frozen=True, kw_only=True)
class BittrexOrderHistoryRecord:
    source_id: str
    occurred_at: datetime | None
    record_type: str
    asset: str
    quantity: Decimal | None
    price: Decimal | None
    cost: Decimal | None
    fee_quantity: Decimal | None
    fee_asset: str | None
    market: str
    raw_type: str

    @property
    def canonical_key(self) -> str:
        return f"bittrex:order:{self.source_id}"

    def transformation_payload(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "occurred_at": self.occurred_at.isoformat() if self.occurred_at else "",
            "record_type": self.record_type,
            "asset": self.asset,
            "quantity": str(self.quantity) if self.quantity is not None else "",
            "price": str(self.price) if self.price is not None else "",
            "cost": str(self.cost) if self.cost is not None else "",
            "fee_quantity": (
                str(self.fee_quantity) if self.fee_quantity is not None else ""
            ),
            "fee_asset": self.fee_asset or "",
            "market": self.market,
            "raw_type": self.raw_type,
        }

    def raw_record(self) -> RawRecordInput:
        return RawRecordInput(
            payload=self.transformation_payload(),
            external_id=f"bittrex:order:{self.source_id}",
            canonical_key=self.canonical_key,
            technical_metadata={
                "source_kind": BITTREX_SOURCE,
                "normalization_version": HISTORICAL_SOURCE_VERSION,
                "evidence_level": "original_exchange",
                "canonical_fingerprint": canonical_sha256(
                    self.transformation_payload()
                ),
            },
        )


def parse_bittrex_order_history(raw: dict[str, Any]) -> BittrexOrderHistoryRecord:
    source_id = _text(_first(raw, "source_id", "order_id", "uuid", "OrderUuid"))
    if not source_id:
        raise ValueError("Bittrex order row requires a stable order UUID")
    raw_type = _text(_first(raw, "record_type", "type", "side", "OrderType"))
    return BittrexOrderHistoryRecord(
        source_id=source_id,
        occurred_at=_timestamp(
            _first(raw, "occurred_at", "timestamp", "date", "time", "TimeStamp")
        ),
        record_type=_bittrex_type(raw_type),
        asset=_text(_first(raw, "asset", "currency", "coin")).upper(),
        quantity=_decimal(_first(raw, "quantity", "Quantity", "volume", "Volume")),
        price=_decimal(_first(raw, "price", "Price")),
        cost=_decimal(_first(raw, "cost", "Total")),
        fee_quantity=_decimal(
            _first(raw, "fee_quantity", "withdrawal_fee", "Commission", "fee")
        ),
        fee_asset=(_text(_first(raw, "fee_asset", "currency", "Currency")) or None),
        market=_text(_first(raw, "market", "Market")),
        raw_type=raw_type,
    )


def parse_bitcoin_de_records(
    records: list[dict[str, Any]],
) -> list[RawRecordInput]:
    return [parse_bitcoin_de_statement(record).raw_record() for record in records]


def parse_bittrex_records(records: list[dict[str, Any]]) -> list[RawRecordInput]:
    return [parse_bittrex_order_history(record).raw_record() for record in records]


def _bitcoin_de_csv_row(row: dict[str, str]) -> dict[str, Any]:
    asset = _text(row.get("Währung")).upper()
    kind = _text(row.get("Typ"))
    normalized_kind = _bitcoin_de_type(kind)
    reference = _text(row.get("Referenz"))
    occurred_at = _source_timestamp(row.get("Datum"))
    movement = _decimal(row.get("Zu- / Abgang"))
    amount_after = _decimal(row.get(f"{asset} nach Bitcoin.de-Gebühr"))
    cost_after = _decimal(row.get("Menge nach Bitcoin.de-Gebühr"))
    cost_unit = _text(row.get("Einheit (Menge nach Bitcoin.de-Gebühr)"))
    if normalized_kind == "buy":
        quantity = amount_after if amount_after is not None else movement
        cost_eur = cost_after if cost_unit == "EUR" else None
        source_id = reference
    elif normalized_kind in {"withdrawal", "network_fee"}:
        quantity = None
        cost_eur = None
        source_id = f"{reference}:{normalized_kind}"
    else:
        quantity = movement
        cost_eur = None
        source_id = reference or f"{asset}:{kind}:{row.get('Datum', '')}"
    fee_quantity = None
    if normalized_kind == "network_fee" and movement is not None:
        fee_quantity = abs(movement)
    return {
        "source_id": source_id,
        "occurred_at": occurred_at.isoformat() if occurred_at else "",
        "record_type": normalized_kind,
        "asset": asset,
        "quantity": str(quantity) if quantity is not None else "",
        "cost_eur": str(cost_eur) if cost_eur is not None else "",
        "fee_quantity": str(fee_quantity) if fee_quantity is not None else "",
        "fee_asset": asset if fee_quantity is not None else "",
        "reference": reference,
        "raw_type": kind,
    }


def parse_bitcoin_de_csv(path: str | Path) -> list[RawRecordInput]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        rows = csv.DictReader(handle, delimiter=";")
        return parse_bitcoin_de_records([_bitcoin_de_csv_row(row) for row in rows])


def _bittrex_csv_row(row: dict[str, str]) -> dict[str, Any]:
    exchange = _text(row.get("Exchange"))
    asset = exchange.rsplit("-", maxsplit=1)[-1].upper()
    order_type = _text(row.get("OrderType"))
    record_type = _bittrex_type(order_type)
    source_id = _text(row.get("Uuid"))
    occurred_at = _source_timestamp(row.get("TimeStamp"))
    return {
        "source_id": source_id,
        "occurred_at": occurred_at.isoformat() if occurred_at else "",
        "record_type": record_type,
        "asset": asset,
        "quantity": _text(row.get("Quantity")),
        "price": _text(row.get("PricePerUnit") or row.get("Price")),
        "cost": _text(row.get("Price")),
        "fee_quantity": "",
        "fee_asset": "",
        "market": exchange,
        "raw_type": order_type,
    }


def parse_bittrex_csv(path: str | Path) -> list[RawRecordInput]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        rows = csv.DictReader(handle)
        return parse_bittrex_records([_bittrex_csv_row(row) for row in rows])
