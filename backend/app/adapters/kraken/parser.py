import csv
import io
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.adapters.kraken.models import (
    KrakenCsvBatch,
    KrakenExportKind,
    KrakenLedgerRow,
    KrakenParsedRow,
    KrakenTradeRow,
)
from app.imports.errors import IssueCategory, ValidationIssue

LEDGER_REQUIRED = frozenset({"txid", "time", "type", "asset", "amount", "fee"})
LEDGER_KNOWN = LEDGER_REQUIRED | {"subtype", "aclass", "balance"}
TRADE_REQUIRED = frozenset(
    {
        "txid",
        "ordertxid",
        "pair",
        "time",
        "type",
        "ordertype",
        "price",
        "cost",
        "fee",
        "vol",
    }
)
TRADE_KNOWN = TRADE_REQUIRED | {"margin", "misc", "ledgers"}
DECIMAL_PATTERN = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
MAX_FIELD_SIZE = 1_048_576
MAX_COLUMNS = 128


class KrakenCsvParser:
    def parse(
        self, raw_data: bytes | str
    ) -> tuple[KrakenCsvBatch | None, tuple[ValidationIssue, ...]]:
        text, encoding_error = self._decode(raw_data)
        if encoding_error is not None:
            return None, (encoding_error,)
        assert text is not None
        if not text:
            return None, (
                self._issue("kraken_csv_empty", "CSV data must not be empty."),
            )

        previous_limit = csv.field_size_limit()
        csv.field_size_limit(MAX_FIELD_SIZE)
        try:
            return self._parse_text(text)
        except csv.Error:
            return None, (
                self._issue("kraken_csv_malformed", "CSV data is malformed."),
            )
        finally:
            csv.field_size_limit(previous_limit)

    def _decode(
        self, raw_data: bytes | str
    ) -> tuple[str | None, ValidationIssue | None]:
        if isinstance(raw_data, str):
            return raw_data.removeprefix("\ufeff"), None
        try:
            return raw_data.decode("utf-8-sig"), None
        except UnicodeDecodeError:
            return None, self._issue(
                "kraken_csv_invalid_utf8", "CSV data must be valid UTF-8."
            )

    def _parse_text(
        self, text: str
    ) -> tuple[KrakenCsvBatch | None, tuple[ValidationIssue, ...]]:
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        headers = next(reader)
        if len(headers) == 1 and ";" in headers[0]:
            return None, (
                self._issue(
                    "kraken_csv_unsupported_delimiter",
                    "Semicolon-delimited CSV is not supported.",
                ),
            )
        if len(headers) > MAX_COLUMNS:
            return None, (
                self._issue(
                    "kraken_csv_malformed",
                    f"CSV exceeds the maximum of {MAX_COLUMNS} columns.",
                ),
            )
        normalized = tuple(header.strip().lower() for header in headers)
        duplicates = sorted({name for name in normalized if normalized.count(name) > 1})
        if duplicates:
            return None, (
                self._issue(
                    "kraken_csv_duplicate_header",
                    f"Headers collide after normalization: {', '.join(duplicates)}.",
                    field=duplicates[0],
                ),
            )
        kind, detection_error = self._detect(frozenset(normalized))
        if detection_error is not None:
            return None, (detection_error,)
        assert kind is not None

        rows: list[KrakenParsedRow] = []
        issues: list[ValidationIssue] = []
        seen_txids: set[str] = set()
        for sequence, values in enumerate(reader):
            source_line = reader.line_num
            if len(values) != len(headers):
                issues.append(
                    self._issue(
                        "kraken_csv_malformed",
                        "CSV row has a different number of columns than its header.",
                        line=source_line,
                    )
                )
                continue
            normalized_values = dict(zip(normalized, values, strict=True))
            original_values = dict(zip(headers, values, strict=True))
            row_issues = self._validate_required(
                normalized_values,
                LEDGER_REQUIRED if kind is KrakenExportKind.LEDGERS else TRADE_REQUIRED,
                source_line,
            )
            if not row_issues:
                txid = normalized_values["txid"]
                if txid in seen_txids:
                    row_issues.append(
                        self._issue(
                            "kraken_csv_duplicate_txid",
                            f"Duplicate Kraken transaction ID at line {source_line}.",
                            line=source_line,
                            field="txid",
                        )
                    )
                else:
                    seen_txids.add(txid)
            row, parse_issues = self._parse_row(
                kind,
                original_values,
                normalized_values,
                source_line,
                sequence,
                headers,
                normalized,
            )
            row_issues.extend(parse_issues)
            issues.extend(row_issues)
            if not row_issues and row is not None:
                rows.append(row)
        if not rows and not issues:
            return None, (
                self._issue(
                    "kraken_csv_header_only", "CSV must contain at least one data row."
                ),
            )
        if issues:
            return None, tuple(issues)
        return (
            KrakenCsvBatch(
                export_kind=kind,
                original_headers=tuple(headers),
                normalized_headers=normalized,
                rows=tuple(rows),
            ),
            (),
        )

    def _detect(
        self, headers: frozenset[str]
    ) -> tuple[KrakenExportKind | None, ValidationIssue | None]:
        ledger = headers >= LEDGER_REQUIRED
        trades = headers >= TRADE_REQUIRED
        if ledger and trades:
            return None, self._issue(
                "kraken_csv_mixed_export_kind",
                "CSV headers match both Kraken export kinds.",
            )
        if ledger:
            return KrakenExportKind.LEDGERS, None
        if trades:
            return KrakenExportKind.TRADES, None
        ledger_signal = bool(
            headers & {"asset", "amount", "aclass", "subtype", "balance"}
        )
        trade_signal = bool(
            headers & {"ordertxid", "pair", "ordertype", "price", "cost", "vol"}
        )
        if ledger_signal and trade_signal:
            return None, self._issue(
                "kraken_csv_mixed_export_kind",
                "CSV combines Kraken ledger and trade columns.",
            )
        candidate = (
            LEDGER_REQUIRED
            if ledger_signal
            else TRADE_REQUIRED if trade_signal else None
        )
        if candidate is not None:
            missing = sorted(candidate - headers)
            return None, self._issue(
                "kraken_csv_missing_required_columns",
                f"Missing required columns: {', '.join(missing)}.",
            )
        return None, self._issue(
            "kraken_csv_unknown_export_kind",
            "CSV headers do not match a supported Kraken export.",
        )

    def _validate_required(
        self, values: dict[str, str], required: frozenset[str], line: int
    ) -> list[ValidationIssue]:
        return [
            self._issue(
                "kraken_csv_empty_required_value",
                f"Required field {field} is empty at line {line}.",
                line=line,
                field=field,
            )
            for field in sorted(required)
            if not values[field]
        ]

    def _parse_row(
        self,
        kind: KrakenExportKind,
        original: dict[str, str],
        normalized_values: dict[str, str],
        line: int,
        sequence: int,
        original_headers: list[str],
        normalized_headers: tuple[str, ...],
    ) -> tuple[KrakenParsedRow | None, list[ValidationIssue]]:
        issues: list[ValidationIssue] = []
        occurred_at = self._timestamp(normalized_values.get("time", ""), line, issues)
        known = LEDGER_KNOWN if kind is KrakenExportKind.LEDGERS else TRADE_KNOWN
        extras = {
            original_headers[index]: normalized_values[name]
            for index, name in enumerate(normalized_headers)
            if name not in known
        }
        metadata: dict[str, Any] = {
            "export_kind": kind.value,
            "source_line": line,
            "sequence_number": sequence,
            "normalized_headers": list(normalized_headers),
            "extra_fields": extras,
        }
        common: dict[str, Any] = {
            "original_values": original,
            "normalized_values": normalized_values,
            "source_line": line,
            "sequence_number": sequence,
            "txid": normalized_values.get("txid", ""),
            "export_kind": kind,
            "occurred_at": occurred_at or datetime.min.replace(tzinfo=UTC),
            "extra_fields": extras,
            "technical_metadata": metadata,
        }
        if kind is KrakenExportKind.LEDGERS:
            amount = self._decimal(
                normalized_values.get("amount", ""), "amount", line, issues
            )
            fee = self._decimal(normalized_values.get("fee", ""), "fee", line, issues)
            balance = self._decimal(
                normalized_values.get("balance", ""),
                "balance",
                line,
                issues,
                optional=True,
            )
            if issues:
                return None, issues
            assert amount is not None and fee is not None
            return (
                KrakenLedgerRow(**common, amount=amount, fee=fee, balance=balance),
                [],
            )
        price = self._decimal(normalized_values.get("price", ""), "price", line, issues)
        cost = self._decimal(normalized_values.get("cost", ""), "cost", line, issues)
        fee = self._decimal(normalized_values.get("fee", ""), "fee", line, issues)
        volume = self._decimal(normalized_values.get("vol", ""), "vol", line, issues)
        margin = self._decimal(
            normalized_values.get("margin", ""), "margin", line, issues, optional=True
        )
        if issues:
            return None, issues
        assert (
            price is not None
            and cost is not None
            and fee is not None
            and volume is not None
        )
        ledger_raw = normalized_values.get("ledgers", "")
        references = tuple(
            item.strip() for item in ledger_raw.split(",") if item.strip()
        )
        return (
            KrakenTradeRow(
                **common,
                price=price,
                cost=cost,
                fee=fee,
                volume=volume,
                margin=margin,
                ledger_references=references,
            ),
            [],
        )

    def _timestamp(
        self, value: str, line: int, issues: list[ValidationIssue]
    ) -> datetime | None:
        try:
            parsed = (
                datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f")
                if "." in value
                else datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            )
            return parsed.replace(tzinfo=UTC)
        except ValueError:
            issues.append(
                self._issue(
                    "kraken_csv_invalid_timestamp",
                    f"Invalid UTC timestamp at line {line}.",
                    line=line,
                    field="time",
                )
            )
            return None

    def _decimal(
        self,
        value: str,
        field: str,
        line: int,
        issues: list[ValidationIssue],
        *,
        optional: bool = False,
    ) -> Decimal | None:
        if not value and optional:
            return None
        if not DECIMAL_PATTERN.fullmatch(value):
            issues.append(
                self._issue(
                    "kraken_csv_invalid_decimal",
                    f"Invalid decimal in field {field} at line {line}.",
                    line=line,
                    field=field,
                )
            )
            return None
        return Decimal(value)

    @staticmethod
    def _issue(
        code: str,
        message: str,
        *,
        line: int | None = None,
        field: str | None = None,
    ) -> ValidationIssue:
        return ValidationIssue(
            code=code,
            message=message,
            category=IssueCategory.VALIDATION,
            record_position=line,
            field=field,
        )
