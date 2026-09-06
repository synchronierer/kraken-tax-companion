from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from app.adapters.kraken.assets import FIAT_ASSETS, resolve_asset
from app.core.entities import RawImportRecord
from app.core.financial_review import (
    FinancialReviewType,
    FinancialSuggestionType,
    ReviewConfidence,
    TaxMappingStatus,
)

STABLE_PROCEEDS_ASSETS = frozenset({"EUR", "USD", "USDC", "USDT"})
DELISTING_WINDOW = timedelta(days=7)


@dataclass(frozen=True, kw_only=True)
class SuggestedReview:
    suggestion_type: FinancialSuggestionType
    confidence: ReviewConfidence
    reasons: tuple[str, ...]
    records: tuple[tuple[RawImportRecord, str], ...]
    metadata: dict[str, Any]


@dataclass(frozen=True, kw_only=True)
class DelistingCandidate:
    record: RawImportRecord
    amount: Decimal
    timestamp: datetime
    asset: str


def ledger_values(record: RawImportRecord) -> dict[str, str]:
    return {
        str(key).strip().lower(): str(value).strip()
        for key, value in record.payload.items()
    }


def ledger_asset(record: RawImportRecord) -> str:
    raw = ledger_values(record).get("asset", "")
    if not raw:
        raise ValueError("Kraken ledger asset must not be empty.")
    resolved = resolve_asset(raw)
    if resolved.canonical_code is None:
        raise ValueError("Invalid Kraken ledger asset.")
    return resolved.canonical_code


def ledger_time(record: RawImportRecord) -> datetime:
    value = datetime.fromisoformat(ledger_values(record)["time"])
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def ledger_decimal(record: RawImportRecord, name: str) -> Decimal:
    try:
        value = Decimal(ledger_values(record)[name])
    except InvalidOperation as error:
        raise ValueError(f"Invalid Kraken ledger {name}.") from error
    if not value.is_finite():
        raise ValueError(f"Invalid Kraken ledger {name}.")
    return value


def fiat_withdrawal_suggestion(
    record: RawImportRecord, *, has_trade_context: bool
) -> SuggestedReview | None:
    if record.source != "kraken-ledgers":
        return None
    values = ledger_values(record)
    if values.get("type", "").lower() != "withdrawal":
        return None
    try:
        amount = ledger_decimal(record, "amount")
        fee = ledger_decimal(record, "fee")
        asset = ledger_asset(record)
    except (KeyError, ValueError):
        return None
    if amount >= 0 or fee < 0 or asset not in FIAT_ASSETS or has_trade_context:
        return None
    return SuggestedReview(
        suggestion_type=FinancialSuggestionType.OWN_ACCOUNT_FIAT_WITHDRAWAL,
        confidence=ReviewConfidence.HIGH,
        reasons=(
            "Kraken-Ledgerart ist withdrawal.",
            "Fiat-Betrag ist negativ und die Gebühr ist nicht negativ.",
            "Für den Rohdatensatz wurde kein normaler Trade gefunden.",
            "Das Zielkonto ist erst durch den Benutzer zu bestätigen.",
        ),
        records=((record, "withdrawal"),),
        metadata={
            "asset": asset,
            "kraken_ledger_amount": str(amount),
            "withdrawal_fee": str(fee),
            "gross_kraken_debit": str(abs(amount) + fee),
            "destination_relation": "UNCONFIRMED",
        },
    )


def delisting_suggestions(
    records: list[RawImportRecord], *, excluded_record_ids: set[UUID]
) -> list[SuggestedReview]:
    candidates: list[DelistingCandidate] = []
    for record in records:
        if record.source != "kraken-ledgers" or record.id in excluded_record_ids:
            continue
        values = ledger_values(record)
        if values.get("type", "").lower() != "transfer":
            continue
        try:
            amount, fee = (
                ledger_decimal(record, "amount"),
                ledger_decimal(record, "fee"),
            )
            timestamp = ledger_time(record)
            asset = ledger_asset(record)
        except (KeyError, ValueError):
            continue
        if amount != 0 and fee == 0 and timestamp.tzinfo is UTC:
            candidates.append(
                DelistingCandidate(
                    record=record,
                    amount=amount,
                    timestamp=timestamp,
                    asset=asset,
                )
            )
    outgoing = [item for item in candidates if item.amount < 0]
    incoming = [item for item in candidates if item.amount > 0]
    pairs: dict[UUID, list[DelistingCandidate]] = {}
    reverse: dict[UUID, list[DelistingCandidate]] = {}
    for left in outgoing:
        for right in incoming:
            gap = right.timestamp - left.timestamp
            if (
                timedelta(0) <= gap <= DELISTING_WINDOW
                and left.asset != right.asset
                and left.asset not in STABLE_PROCEEDS_ASSETS
                and right.asset in STABLE_PROCEEDS_ASSETS
            ):
                pairs.setdefault(left.record.id, []).append(right)
                reverse.setdefault(right.record.id, []).append(left)
    suggestions: list[SuggestedReview] = []
    for left in outgoing:
        matches = pairs.get(left.record.id, [])
        if len(matches) != 1 or len(reverse.get(matches[0].record.id, [])) != 1:
            continue
        right = matches[0]
        start, end = left.timestamp, right.timestamp
        suggestions.append(
            SuggestedReview(
                suggestion_type=(
                    FinancialSuggestionType.POSSIBLE_DELISTING_LIQUIDATION
                ),
                confidence=ReviewConfidence.MEDIUM,
                reasons=(
                    "Zwei finanziell relevante Transfer-Records haben "
                    "entgegengesetzte Richtungen.",
                    "Die Assets unterscheiden sich und der Eingang ist ein "
                    "Stablecoin/Fiat-Asset.",
                    "Beide Records sind gebührenfrei und liegen höchstens sieben "
                    "Tage auseinander.",
                    "Für keinen Record wurde ein normaler Trade oder eine bestätigte "
                    "Zuordnung gefunden.",
                ),
                records=((left.record, "outgoing"), (right.record, "incoming")),
                metadata={
                    "outgoing_record_id": str(left.record.id),
                    "incoming_record_id": str(right.record.id),
                    "disposed_asset": left.asset,
                    "disposed_quantity": str(abs(left.amount)),
                    "proceeds_asset": right.asset,
                    "proceeds_quantity": str(right.amount),
                    "event_window_start": start.isoformat(),
                    "event_window_end": end.isoformat(),
                    "time_distance_seconds": int((end - start).total_seconds()),
                    "provider_refids": [
                        ledger_values(left.record).get("refid"),
                        ledger_values(right.record).get("refid"),
                    ],
                    "provider_explicitly_linked": False,
                },
            )
        )
    return suggestions


def resolution_metadata(
    resolution_type: FinancialReviewType, records: list[RawImportRecord]
) -> tuple[dict[str, Any], TaxMappingStatus, list[tuple[RawImportRecord, str]]]:
    if resolution_type is FinancialReviewType.OWN_ACCOUNT_FIAT_WITHDRAWAL:
        if len(records) != 1:
            raise ValueError("A fiat withdrawal resolution needs exactly one record.")
        suggestion = fiat_withdrawal_suggestion(records[0], has_trade_context=False)
        if suggestion is None:
            raise ValueError("The record is not a fiat withdrawal review candidate.")
        metadata = {
            **suggestion.metadata,
            "net_external_credit": str(abs(ledger_decimal(records[0], "amount"))),
            "destination_relation": "OWN_ACCOUNT",
            "tax_mapping": "NO_CRYPTO_DISPOSAL",
            "fee_type": "WITHDRAWAL_FEE",
            "fee_tax_status": "REVIEW_REQUIRED",
            "confirmation_source": "USER_CONFIRMED",
        }
        return metadata, TaxMappingStatus.NOT_REQUIRED, list(suggestion.records)
    suggestions = delisting_suggestions(records, excluded_record_ids=set())
    if len(records) != 2 or len(suggestions) != 1:
        raise ValueError("The records are not one delisting liquidation candidate.")
    suggestion = suggestions[0]
    metadata = {**suggestion.metadata, "confirmation_source": "USER_CONFIRMED"}
    return metadata, TaxMappingStatus.PENDING, list(suggestion.records)
