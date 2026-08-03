from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from app.imports.errors import ValidationIssue
from app.imports.service import ImportResult, RawRecordInput


class KrakenExportKind(StrEnum):
    LEDGERS = "kraken-ledgers-csv"
    TRADES = "kraken-trades-csv"

    @property
    def source(self) -> str:
        return {
            self.LEDGERS: "kraken-ledgers",
            self.TRADES: "kraken-trades",
        }[self]

    @property
    def contract_version(self) -> str:
        return f"{self.value}-v1"


@dataclass(frozen=True, kw_only=True)
class KrakenRow:
    original_values: Mapping[str, str]
    normalized_values: Mapping[str, str]
    source_line: int
    sequence_number: int
    txid: str
    export_kind: KrakenExportKind
    occurred_at: datetime
    extra_fields: Mapping[str, str]
    technical_metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        for name in (
            "original_values",
            "normalized_values",
            "extra_fields",
            "technical_metadata",
        ):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))

    def raw_record(self) -> RawRecordInput:
        external_kind = (
            "ledger" if self.export_kind is KrakenExportKind.LEDGERS else "trade"
        )
        metadata = dict(self.technical_metadata)
        if isinstance(self, KrakenLedgerRow) and {
            "refid",
            "subclass",
            "wallet",
        }.issubset({key.lower() for key in self.original_values}):
            from app.adapters.kraken.ledger import (
                LEDGER_ASSET_MAPPING_VERSION,
                canonical_fingerprint,
                csv_row_record,
            )

            canonical = csv_row_record(self)
            metadata["canonical_fingerprint"] = canonical_fingerprint(canonical)
            metadata["asset_mapping_version"] = LEDGER_ASSET_MAPPING_VERSION
            metadata["canonical_asset"] = {
                "raw_asset": canonical.asset_raw,
                "normalized_asset": canonical.asset_normalized,
                "product_marker": canonical.product_marker,
                "product_variant": canonical.product_variant,
                "is_unambiguous": canonical.asset_mapping_known,
            }
        return RawRecordInput(
            payload=dict(self.original_values),
            external_id=f"kraken:{external_kind}:{self.txid}",
            technical_metadata=metadata,
            canonical_key=(
                f"kraken:spot_ledger:{self.txid}"
                if self.export_kind is KrakenExportKind.LEDGERS
                else f"kraken:spot_trades:{self.txid}"
            ),
        )


@dataclass(frozen=True, kw_only=True)
class KrakenLedgerRow(KrakenRow):
    amount: Decimal
    fee: Decimal
    balance: Decimal | None


@dataclass(frozen=True, kw_only=True)
class KrakenTradeRow(KrakenRow):
    price: Decimal
    cost: Decimal
    fee: Decimal
    volume: Decimal
    margin: Decimal | None
    ledger_references: tuple[str, ...]


KrakenParsedRow = KrakenLedgerRow | KrakenTradeRow


@dataclass(frozen=True, kw_only=True)
class KrakenCsvBatch:
    export_kind: KrakenExportKind
    original_headers: tuple[str, ...]
    normalized_headers: tuple[str, ...]
    rows: tuple[KrakenParsedRow, ...]

    def raw_records(self) -> list[RawRecordInput]:
        return [row.raw_record() for row in self.rows]


@dataclass(frozen=True, kw_only=True)
class KrakenCsvImportResult:
    batch: KrakenCsvBatch | None
    import_result: ImportResult | None
    errors: tuple[ValidationIssue, ...] = ()

    @property
    def successful(self) -> bool:
        return self.import_result is not None and not self.errors
