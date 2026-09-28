from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum

from app.adapters.kraken.assets import resolve_pair
from app.adapters.kraken.ledger import CanonicalKrakenLedgerRecord
from app.infrastructure.kraken_private import TradeHistoryEntry

MAX_FALLBACK_TIME_DISTANCE = timedelta(seconds=1)
MAX_QUOTE_ROUNDING_TOLERANCE = Decimal("0.0001")

ExpectedLeg = tuple[str, Decimal, bool]


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
            missing_ledger_ids=(),
        )
    base = pair.base.canonical_code
    quote = pair.quote.canonical_code
    assert base is not None and quote is not None
    expected = (
        ((base, trade.volume, False), (quote, -trade.cost, True))
        if trade.side == "buy"
        else ((base, -trade.volume, False), (quote, trade.cost, True))
    )
    by_id = {item.ledger_id: item for item in ledgers}
    if trade.ledger_ids:
        found = tuple(by_id[item] for item in trade.ledger_ids if item in by_id)
        missing = tuple(item for item in trade.ledger_ids if item not in by_id)
        if not found:
            status = TradeLedgerReconciliationStatus.PENDING
        elif any(
            item.refid != trade.trade_id for item in found
        ) or not _matches_expected(found, expected):
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
        if not item.refid and any(_matches_leg(item, leg) for leg in expected)
    )
    matches_by_leg = tuple(
        tuple(item for item in candidates if _matches_leg(item, leg))
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
    expected: tuple[ExpectedLeg, ExpectedLeg],
) -> bool:
    remaining = list(expected)
    for record in records:
        matches = [
            index for index, leg in enumerate(remaining) if _matches_leg(record, leg)
        ]
        if len(matches) != 1:
            return False
        remaining.pop(matches[0])
    return True


def _matches_leg(
    record: CanonicalKrakenLedgerRecord,
    expected: ExpectedLeg,
) -> bool:
    asset, amount, precision_aware = expected
    if record.normalized_event != "trade" or record.asset_normalized != asset:
        return False
    if record.amount == amount:
        return True
    if not precision_aware or record.amount.is_zero() or amount.is_zero():
        return False
    if record.amount.is_signed() != amount.is_signed():
        return False
    return abs(record.amount - amount) <= _quote_rounding_tolerance(
        record.amount, amount
    )


def _quote_rounding_tolerance(first: Decimal, second: Decimal) -> Decimal:
    first_exponent = first.as_tuple().exponent
    second_exponent = second.as_tuple().exponent
    assert isinstance(first_exponent, int) and isinstance(second_exponent, int)
    provider_quantum = max(
        Decimal(1).scaleb(first_exponent),
        Decimal(1).scaleb(second_exponent),
    )
    return min(provider_quantum, MAX_QUOTE_ROUNDING_TOLERANCE)
