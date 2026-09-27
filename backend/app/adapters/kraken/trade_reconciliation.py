from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum

from app.adapters.kraken.assets import resolve_pair
from app.adapters.kraken.ledger import CanonicalKrakenLedgerRecord
from app.infrastructure.kraken_private import TradeHistoryEntry

MAX_FALLBACK_TIME_DISTANCE = timedelta(seconds=1)


class TradeLedgerReconciliationStatus(StrEnum):
    MATCHED = "MATCHED"
    PARTIAL = "PARTIAL"
    PENDING = "PENDING"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, kw_only=True)
class TradeLedgerReconciliation:
    trade_id: str
    status: TradeLedgerReconciliationStatus
    matched_ledger_ids: tuple[str, ...]
    missing_ledger_ids: tuple[str, ...]


def reconcile_trade_ledgers(
    trade: TradeHistoryEntry,
    ledgers: tuple[CanonicalKrakenLedgerRecord, ...],
) -> TradeLedgerReconciliation:
    pair = resolve_pair(trade.pair)
    if pair is None:
        return TradeLedgerReconciliation(
            trade_id=trade.trade_id,
            status=TradeLedgerReconciliationStatus.CONFLICT,
            matched_ledger_ids=(),
            missing_ledger_ids=trade.ledger_ids,
        )
    base = pair.base.canonical_code
    quote = pair.quote.canonical_code
    assert base is not None and quote is not None
    expected = (
        ((base, trade.volume), (quote, -trade.cost))
        if trade.side == "buy"
        else ((base, -trade.volume), (quote, trade.cost))
    )
    by_id = {item.ledger_id: item for item in ledgers}
    if trade.ledger_ids:
        found = tuple(by_id[item] for item in trade.ledger_ids if item in by_id)
        missing = tuple(item for item in trade.ledger_ids if item not in by_id)
        if not found:
            status = TradeLedgerReconciliationStatus.PENDING
        elif not _matches_expected(found, expected):
            status = TradeLedgerReconciliationStatus.CONFLICT
        elif missing or len(found) != 2:
            status = TradeLedgerReconciliationStatus.PARTIAL
        else:
            status = TradeLedgerReconciliationStatus.MATCHED
        return TradeLedgerReconciliation(
            trade_id=trade.trade_id,
            status=status,
            matched_ledger_ids=tuple(item.ledger_id for item in found),
            missing_ledger_ids=missing,
        )

    temporally_close = tuple(
        item
        for item in ledgers
        if item.normalized_event == "trade"
        and abs(item.occurred_at - trade.occurred_at) <= MAX_FALLBACK_TIME_DISTANCE
    )
    referenced = tuple(
        item for item in temporally_close if item.refid == trade.trade_id
    )
    candidates = referenced or tuple(
        item
        for item in temporally_close
        if (item.asset_normalized, item.amount) in expected
    )
    matches_by_leg = tuple(
        tuple(
            item for item in candidates if (item.asset_normalized, item.amount) == leg
        )
        for leg in expected
    )
    if any(len(items) > 1 for items in matches_by_leg):
        status = TradeLedgerReconciliationStatus.CONFLICT
        matched: tuple[CanonicalKrakenLedgerRecord, ...] = ()
    else:
        matched = tuple(items[0] for items in matches_by_leg if items)
        if referenced and len(referenced) != len(matched):
            status = TradeLedgerReconciliationStatus.CONFLICT
            matched = ()
        elif len(matched) == 2 and len({item.ledger_id for item in matched}) == 2:
            status = TradeLedgerReconciliationStatus.MATCHED
        elif matched:
            status = TradeLedgerReconciliationStatus.PARTIAL
        else:
            status = TradeLedgerReconciliationStatus.PENDING
    return TradeLedgerReconciliation(
        trade_id=trade.trade_id,
        status=status,
        matched_ledger_ids=tuple(item.ledger_id for item in matched),
        missing_ledger_ids=(),
    )


def _matches_expected(
    records: tuple[CanonicalKrakenLedgerRecord, ...],
    expected: tuple[tuple[str, Decimal], tuple[str, Decimal]],
) -> bool:
    actual = {(item.asset_normalized, item.amount) for item in records}
    expected_set = set(expected)
    return (
        all(item.normalized_event == "trade" for item in records)
        and actual.issubset(expected_set)
        and len(actual) == len(records)
    )
