import csv
import hashlib
import io
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

from app.adapters.kraken.assets import (
    ASSET_MAPPING_VERSION,
    KrakenAssetIdentity,
    KrakenAssetNormalizationKind,
    normalize_kraken_asset,
)
from app.infrastructure.kraken_private import LedgerEntry

LEDGER_NORMALIZATION_VERSION = "kraken-ledger-normalization-v2"
LEDGER_ASSET_MAPPING_VERSION = ASSET_MAPPING_VERSION
LEDGER_REQUIRED_COLUMNS = frozenset(
    {
        "txid",
        "refid",
        "time",
        "type",
        "subtype",
        "aclass",
        "subclass",
        "asset",
        "wallet",
        "amount",
        "fee",
        "balance",
    }
)
_DECIMAL = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
_KNOWN_API_TYPES = {
    "adjustment",
    "credit",
    "deposit",
    "dividend",
    "margin",
    "nft_rebate",
    "rollover",
    "sale",
    "settled",
    "staking",
    "trade",
    "transfer",
    "withdrawal",
}
_KNOWN_SUBTYPES = {
    "",
    "migration",
    "onchain",
    "reward",
    "spotfromfutures",
    "spottostaking",
    "stakingfromspot",
    "stakingtospot",
}


class LedgerSourceKind(StrEnum):
    CSV = "kraken_csv"
    LIVE_API = "kraken_live_api"


class CanonicalCsvRow(Protocol):
    @property
    def original_values(self) -> Mapping[str, str]: ...


@dataclass(frozen=True, kw_only=True)
class CanonicalKrakenLedgerRecord:
    ledger_id: str
    refid: str
    occurred_at: datetime
    api_type: str | None
    api_subtype: str | None
    csv_type: str | None
    csv_subtype: str | None
    asset_raw: str
    asset_normalized: str
    asset_normalization_kind: KrakenAssetNormalizationKind
    product_marker: str | None
    product_variant: str | None
    wallet_label: str | None
    amount: Decimal
    fee: Decimal
    balance: Decimal | None
    asset_class: str
    subclass: str | None
    source_kind: LedgerSourceKind
    source_payload: dict[str, Any]
    asset_mapping_known: bool

    @property
    def canonical_key(self) -> str:
        return f"kraken:spot_ledger:{self.ledger_id}"

    @property
    def normalized_event(self) -> str | None:
        if self.api_type is not None:
            return "earn_reward" if self.api_type == "staking" else self.api_type
        if self.csv_type == "earn" and self.csv_subtype == "reward":
            return "earn_reward"
        if self.csv_type:
            return self.csv_type
        return None

    @property
    def event_mapping_known(self) -> bool:
        if self.api_type is not None:
            return (
                self.api_type in _KNOWN_API_TYPES
                and (self.api_subtype or "") in _KNOWN_SUBTYPES
            )
        if self.csv_type == "earn":
            return self.csv_subtype == "reward"
        return (
            self.csv_type in _KNOWN_API_TYPES
            and (self.csv_subtype or "") in _KNOWN_SUBTYPES
        )

    def import_payload(self) -> dict[str, Any]:
        return dict(self.source_payload)


@dataclass(frozen=True, kw_only=True)
class ParsedLedgerBatch:
    records: tuple[CanonicalKrakenLedgerRecord, ...]
    duplicate_ids: tuple[str, ...]
    conflicting_duplicate_ids: tuple[str, ...]
    malformed_entries: int
    warnings: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class LedgerComparison:
    csv_total: int
    api_total: int
    csv_unique_total: int
    api_unique_total: int
    matched_ids: int
    missing_in_api: tuple[str, ...]
    missing_in_csv: tuple[str, ...]
    field_mismatch_count: int
    mismatches_by_field: dict[str, int]
    exact_match_count: int
    normalized_match_count: int
    timestamp_precision_only_count: int
    not_comparable_fields: tuple[str, ...]
    unknown_asset_mappings: tuple[str, ...]
    unknown_type_mappings: tuple[str, ...]
    csv_digest: str
    api_digest: str
    warnings: tuple[str, ...]
    ready_for_import: bool
    diagnostic_ids: tuple[str, ...]


def normalize_asset(raw: str) -> KrakenAssetIdentity:
    """Compatibility name for the shared Kraken adapter normalizer."""

    return normalize_kraken_asset(raw)


def _decimal(value: str, *, optional: bool = False) -> Decimal | None:
    if optional and not value:
        return None
    if not _DECIMAL.fullmatch(value):
        raise ValueError("Ungültiger Decimalwert im Kraken-Ledger.")
    return Decimal(value)


def parse_ledger_csv(raw_data: bytes | str) -> ParsedLedgerBatch:
    try:
        text = (
            raw_data.removeprefix("\ufeff")
            if isinstance(raw_data, str)
            else raw_data.decode("utf-8-sig")
        )
    except UnicodeDecodeError as error:
        raise ValueError("Die Ledger-CSV ist nicht UTF-8-kodiert.") from error
    if not text:
        raise ValueError("Die Ledger-CSV ist leer.")
    reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
    headers = {item.strip().lower() for item in (reader.fieldnames or ())}
    missing = sorted(LEDGER_REQUIRED_COLUMNS - headers)
    if missing:
        raise ValueError(f"Fehlende Ledger-Spalten: {', '.join(missing)}.")
    records: dict[str, CanonicalKrakenLedgerRecord] = {}
    duplicates: set[str] = set()
    conflicts: set[str] = set()
    malformed = 0
    warnings: list[str] = []
    for row in reader:
        if any(
            key is None or not isinstance(value, (str, type(None)))
            for key, value in row.items()
        ):
            malformed += 1
            continue
        values = {key.strip().lower(): value for key, value in row.items()}
        try:
            record = _csv_record(values)
        except (KeyError, TypeError, ValueError):
            malformed += 1
            continue
        previous = records.get(record.ledger_id)
        if previous:
            duplicates.add(record.ledger_id)
            if previous != record:
                conflicts.add(record.ledger_id)
        else:
            records[record.ledger_id] = record
    if not records and malformed == 0:
        raise ValueError("Die Ledger-CSV enthält keine Datensätze.")
    if malformed:
        warnings.append("Die CSV enthält fehlerhafte Pflichtdatensätze.")
    return ParsedLedgerBatch(
        records=tuple(sorted(records.values(), key=_record_order)),
        duplicate_ids=tuple(sorted(duplicates)),
        conflicting_duplicate_ids=tuple(sorted(conflicts)),
        malformed_entries=malformed,
        warnings=tuple(warnings),
    )


def _csv_record(values: dict[str, str | None]) -> CanonicalKrakenLedgerRecord:
    required = {key: values.get(key) for key in LEDGER_REQUIRED_COLUMNS}
    if any(value is None for value in required.values()):
        raise ValueError("Unvollständiger Ledger-Datensatz.")
    ledger_id = (required["txid"] or "").strip()
    if not ledger_id:
        raise ValueError("Ledger-ID fehlt.")
    occurred = datetime.strptime(required["time"] or "", "%Y-%m-%d %H:%M:%S")
    asset = normalize_asset(required["asset"] or "")
    amount = _decimal(required["amount"] or "")
    fee = _decimal(required["fee"] or "")
    assert amount is not None and fee is not None
    payload = {key: value or "" for key, value in values.items()}
    return CanonicalKrakenLedgerRecord(
        ledger_id=ledger_id,
        refid=(required["refid"] or "").strip(),
        occurred_at=occurred.replace(tzinfo=UTC),
        api_type=None,
        api_subtype=None,
        csv_type=(required["type"] or "").strip().lower(),
        csv_subtype=(required["subtype"] or "").strip().lower(),
        asset_raw=required["asset"] or "",
        asset_normalized=asset.normalized_asset or "",
        asset_normalization_kind=asset.alias_kind,
        product_marker=asset.product_marker,
        product_variant=asset.product_variant,
        wallet_label=(required["wallet"] or "").strip() or None,
        amount=amount,
        fee=fee,
        balance=_decimal(required["balance"] or "", optional=True),
        asset_class=(required["aclass"] or "").strip(),
        subclass=(required["subclass"] or "").strip() or None,
        source_kind=LedgerSourceKind.CSV,
        source_payload=payload,
        asset_mapping_known=asset.is_unambiguous,
    )


def csv_row_record(row: CanonicalCsvRow) -> CanonicalKrakenLedgerRecord:
    return _csv_record(
        {str(key).lower(): str(value) for key, value in row.original_values.items()}
    )


def canonical_from_api(entry: LedgerEntry) -> CanonicalKrakenLedgerRecord:
    asset = normalize_asset(entry.asset)
    extra = dict(entry.extra)
    import_type = "earn" if entry.entry_type == "staking" else entry.entry_type
    import_subtype = "reward" if entry.entry_type == "staking" else entry.subtype
    timestamp = entry.occurred_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    retained_extra = {
        str(key): str(value)
        for key, value in extra.items()
        if key not in {"aclass", "balance", "refid", "subclass"}
    }
    return CanonicalKrakenLedgerRecord(
        ledger_id=entry.ledger_id,
        refid=str(extra.get("refid", "")),
        occurred_at=entry.occurred_at,
        api_type=entry.entry_type,
        api_subtype=entry.subtype,
        csv_type=None,
        csv_subtype=None,
        asset_raw=entry.asset,
        asset_normalized=asset.normalized_asset or "",
        asset_normalization_kind=asset.alias_kind,
        product_marker=asset.product_marker,
        product_variant=asset.product_variant,
        wallet_label=None,
        amount=entry.amount,
        fee=entry.fee,
        balance=(Decimal(str(extra["balance"])) if "balance" in extra else None),
        asset_class=str(extra.get("aclass", "currency")),
        subclass=str(extra["subclass"]) if extra.get("subclass") else None,
        source_kind=LedgerSourceKind.LIVE_API,
        source_payload={
            "txid": entry.ledger_id,
            "refid": str(extra.get("refid", "")),
            "time": timestamp,
            "type": import_type,
            "subtype": import_subtype,
            "aclass": str(extra.get("aclass", "currency")),
            "subclass": str(extra.get("subclass", "")),
            "asset": entry.asset,
            "wallet": "",
            "amount": str(entry.amount),
            "fee": str(entry.fee),
            "balance": str(extra.get("balance", "")),
            "api_type": entry.entry_type,
            "api_subtype": entry.subtype,
            "api_occurred_at": entry.occurred_at.isoformat(),
            "provider_extra": retained_extra,
        },
        asset_mapping_known=asset.is_unambiguous,
    )


def filter_records(
    records: tuple[CanonicalKrakenLedgerRecord, ...],
    start: datetime | None,
    end: datetime | None,
) -> tuple[CanonicalKrakenLedgerRecord, ...]:
    return tuple(
        item
        for item in records
        if (start is None or start <= item.occurred_at)
        and (end is None or item.occurred_at < end)
    )


def ledger_digest(records: tuple[CanonicalKrakenLedgerRecord, ...]) -> str:
    return hashlib.sha256(
        "\n".join(sorted(item.ledger_id for item in records)).encode("utf-8")
    ).hexdigest()


def canonical_fingerprint(record: CanonicalKrakenLedgerRecord) -> str:
    values = (
        record.ledger_id,
        record.refid,
        record.occurred_at.replace(microsecond=0).isoformat(),
        record.asset_normalized,
        str(record.amount),
        str(record.fee),
        record.normalized_event or "",
    )
    return hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()


def compare_ledgers(
    csv_batch: ParsedLedgerBatch,
    api_records: tuple[CanonicalKrakenLedgerRecord, ...],
    *,
    diagnostic_limit: int,
) -> LedgerComparison:
    csv_by_id = {item.ledger_id: item for item in csv_batch.records}
    api_by_id = {item.ledger_id: item for item in api_records}
    common = sorted(set(csv_by_id) & set(api_by_id))
    missing_api = tuple(sorted(set(csv_by_id) - set(api_by_id)))
    missing_csv = tuple(sorted(set(api_by_id) - set(csv_by_id)))
    mismatches: Counter[str] = Counter()
    exact = 0
    normalized = 0
    precision_only = 0
    unknown_types: set[str] = set()
    diagnostic_ids: list[str] = []
    for ledger_id in common:
        csv_item = csv_by_id[ledger_id]
        api_item = api_by_id[ledger_id]
        fields: list[str] = []
        for field in ("refid", "asset_normalized", "amount", "fee"):
            if getattr(csv_item, field) != getattr(api_item, field):
                fields.append(field)
        csv_second = csv_item.occurred_at.replace(microsecond=0)
        api_second = api_item.occurred_at.replace(microsecond=0)
        if csv_second != api_second:
            fields.append("occurred_at")
        elif csv_item.occurred_at != api_item.occurred_at:
            precision_only += 1
        if (
            csv_item.balance is not None
            and api_item.balance is not None
            and csv_item.balance != api_item.balance
        ):
            fields.append("balance")
        if not csv_item.event_mapping_known or not api_item.event_mapping_known:
            unknown_types.add(ledger_id)
        elif csv_item.normalized_event != api_item.normalized_event:
            fields.append("event_type")
        if fields:
            mismatches.update(fields)
            diagnostic_ids.append(ledger_id)
        elif (
            csv_item.csv_type == api_item.api_type
            and csv_item.csv_subtype == api_item.api_subtype
        ):
            exact += 1
        else:
            normalized += 1
    csv_digest = ledger_digest(csv_batch.records)
    api_digest = ledger_digest(api_records)
    unknown_assets = tuple(
        sorted(
            {
                item.asset_raw
                for item in (*csv_batch.records, *api_records)
                if not item.asset_mapping_known
            }
        )
    )
    ready = not any(
        (
            missing_api,
            missing_csv,
            csv_batch.conflicting_duplicate_ids,
            mismatches,
            unknown_types,
            unknown_assets,
            csv_batch.malformed_entries,
            csv_digest != api_digest,
        )
    )
    return LedgerComparison(
        csv_total=len(csv_batch.records) + len(csv_batch.duplicate_ids),
        api_total=len(api_records),
        csv_unique_total=len(csv_batch.records),
        api_unique_total=len(api_records),
        matched_ids=len(common),
        missing_in_api=missing_api,
        missing_in_csv=missing_csv,
        field_mismatch_count=sum(mismatches.values()),
        mismatches_by_field=dict(sorted(mismatches.items())),
        exact_match_count=exact,
        normalized_match_count=normalized,
        timestamp_precision_only_count=precision_only,
        not_comparable_fields=("wallet",) if common else (),
        unknown_asset_mappings=unknown_assets,
        unknown_type_mappings=tuple(sorted(unknown_types)),
        csv_digest=csv_digest,
        api_digest=api_digest,
        warnings=csv_batch.warnings,
        ready_for_import=ready,
        diagnostic_ids=tuple(diagnostic_ids[:diagnostic_limit]),
    )


def _record_order(item: CanonicalKrakenLedgerRecord) -> tuple[datetime, str]:
    return item.occurred_at, item.ledger_id
