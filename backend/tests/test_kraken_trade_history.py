import base64
import hashlib
import inspect
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs
from urllib.request import Request

import pytest
from fastapi.testclient import TestClient

from app.adapters.kraken.ledger import canonical_from_api
from app.adapters.kraken.trade_reconciliation import (
    TradeLedgerReconciliationStatus,
    reconcile_trade_ledgers,
)
from app.api import kraken_live
from app.config.settings import Settings, get_settings
from app.infrastructure.kraken_private import (
    KrakenPrivateClient,
    KrakenPrivateError,
    LedgerEntry,
    MonotonicNonce,
    TradeHistoryEntry,
)
from app.main import app

SECRET = base64.b64encode(b"synthetic-trade-secret").decode()
START = datetime(2021, 1, 1, tzinfo=UTC)


class Response:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode()

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def raw_trade(
    trade_id: str,
    *,
    timestamp: str = "1609459200.125",
    side: str = "buy",
    pair: str = "DOTEUR",
    price: str = "7.996798666666666666666666667",
    cost: str = "599.7599",
    fee: str = "1.5594",
    volume: str = "75",
    ledgers: object = ("L-EUR", "L-DOT"),
) -> tuple[str, dict[str, object]]:
    return trade_id, {
        "ordertxid": f"ORDER-{trade_id}",
        "postxid": "",
        "pair": pair,
        "time": timestamp,
        "type": side,
        "ordertype": "market",
        "price": price,
        "cost": cost,
        "fee": fee,
        "vol": volume,
        "margin": "0",
        "leverage": "none",
        "misc": "",
        "ledgers": list(ledgers) if isinstance(ledgers, tuple) else ledgers,
        "synthetic_note": "fixture",
    }


def client(
    *pages: object,
    max_pages: int = 10_000,
    requests: list[dict[str, list[str]]] | None = None,
) -> KrakenPrivateClient:
    iterator = iter(pages)

    def opener(request: Request, timeout: float) -> Response:
        if requests is not None:
            requests.append(parse_qs((request.data or b"").decode()))
        value = next(iterator)
        if isinstance(value, Exception):
            raise value
        return Response(value)

    return KrakenPrivateClient(
        api_key="query-only-key",
        api_secret=SECRET,
        max_retries=0,
        max_pages=max_pages,
        ledger_min_interval_seconds=1,
        nonce=MonotonicNonce(lambda: 1000),
        opener=opener,
        sleeper=lambda delay: None,
    )


def page(count: int, *trades: tuple[str, dict[str, object]]) -> dict[str, object]:
    return {"error": [], "result": {"count": count, "trades": dict(trades)}}


def history_trade(
    *,
    trade_id: str = "SYNTHETIC-TRADE",
    side: str = "buy",
    ledger_ids: tuple[str, ...] = (),
    pair: str = "DOTEUR",
    cost: str = "599.7599",
    volume: str = "75",
) -> TradeHistoryEntry:
    return KrakenPrivateClient._parse_trade(
        trade_id,
        raw_trade(
            trade_id,
            side=side,
            ledgers=ledger_ids,
            pair=pair,
            cost=cost,
            volume=volume,
        )[1],
    )


def ledger(
    ledger_id: str,
    asset: str,
    amount: str,
    *,
    refid: str = "SYNTHETIC-TRADE",
    occurred_at: datetime = START + timedelta(microseconds=125000),
    entry_type: str = "trade",
):
    return canonical_from_api(
        LedgerEntry(
            ledger_id=ledger_id,
            occurred_at=occurred_at,
            entry_type=entry_type,
            subtype="",
            asset=asset,
            amount=Decimal(amount),
            fee=Decimal("0"),
            extra={"refid": refid},
        )
    )


def test_trade_history_one_page_preserves_exact_domain_preview() -> None:
    requests: list[dict[str, list[str]]] = []
    preview = client(
        page(1, raw_trade("SYNTHETIC-DOT")), requests=requests
    ).trade_history_preview(
        start=START,
        end=START + timedelta(days=1),
        diagnostic_limit=1,
    )
    assert preview.fetched_pages == 1
    assert preview.reported_total == preview.received_total == preview.unique_total == 1
    assert preview.pagination_complete is True
    assert preview.ready_for_import is True
    assert preview.counts_by_pair == {"DOTEUR": 1}
    assert preview.counts_by_side == {"buy": 1}
    trade = preview.records[0]
    assert trade.price == Decimal("7.996798666666666666666666667")
    assert trade.cost == Decimal("599.7599")
    assert trade.fee == Decimal("1.5594")
    assert trade.volume == Decimal("75")
    assert trade.ledger_ids == ("L-EUR", "L-DOT")
    assert trade.provider_metadata == {"synthetic_note": "fixture"}
    assert trade.transformation_payload()["pair"] == "DOTEUR"
    assert trade.transformation_payload()["ledgers"] == "L-EUR,L-DOT"
    assert preview.diagnostics[0].trade_id == "SYNTHETIC-DOT"
    assert requests[0]["type"] == ["all"]
    assert requests[0]["trades"] == ["false"]
    assert requests[0]["ledgers"] == ["true"]
    assert requests[0]["ofs"] == ["0"]
    assert requests[0]["start"] == ["1609459199"]
    assert requests[0]["end"] == ["1609545600"]


def test_trade_history_multiple_pages_and_stable_digest_order() -> None:
    first = tuple(raw_trade(f"SYNTHETIC-{index:03}") for index in range(50))
    second = (raw_trade("SYNTHETIC-050", side="sell"),)
    requests: list[dict[str, list[str]]] = []
    preview = client(
        page(51, *first), page(51, *second), requests=requests
    ).trade_history_preview(start=None, end=None, diagnostic_limit=0)
    expected = hashlib.sha256(
        "\n".join(f"SYNTHETIC-{index:03}" for index in range(51)).encode()
    ).hexdigest()
    assert preview.fetched_pages == 2
    assert preview.received_total == preview.unique_total == 51
    assert preview.counts_by_side == {"buy": 50, "sell": 1}
    assert preview.stable_trade_id_digest == expected
    assert requests[1]["ofs"] == ["50"]


def test_trade_history_empty_and_local_boundary_filtering() -> None:
    empty = client(page(0)).trade_history_preview(
        start=None, end=None, diagnostic_limit=0
    )
    assert empty.records == ()
    assert empty.earliest_trade_at is empty.latest_trade_at is None
    assert empty.stable_trade_id_digest == hashlib.sha256(b"").hexdigest()
    filtered = client(
        page(
            2,
            raw_trade("BEFORE", timestamp="1609459199.999"),
            raw_trade("INSIDE", timestamp="1609459200.125"),
        )
    ).trade_history_preview(
        start=START,
        end=START + timedelta(seconds=1),
        diagnostic_limit=1,
    )
    assert [item.trade_id for item in filtered.records] == ["INSIDE"]
    assert filtered.requested_start == START
    assert filtered.requested_end == START + timedelta(seconds=1)


@pytest.mark.parametrize("conflicting", [False, True])
def test_trade_history_tracks_identical_and_conflicting_duplicates(
    conflicting: bool,
) -> None:
    duplicate = raw_trade("DUPLICATE", cost="600" if conflicting else "599.7599")
    preview = client(
        page(3, raw_trade("DUPLICATE"), raw_trade("OTHER")),
        page(3, duplicate),
    ).trade_history_preview(start=None, end=None, diagnostic_limit=0)
    assert preview.duplicate_ids == ("DUPLICATE",)
    assert preview.conflicting_duplicate_ids == (("DUPLICATE",) if conflicting else ())
    assert preview.ready_for_import is False


def test_trade_history_reports_no_progress_and_max_pages() -> None:
    stalled = client(page(1)).trade_history_preview(
        start=None, end=None, diagnostic_limit=0
    )
    assert stalled.pagination_complete is False
    assert stalled.ready_for_import is False
    assert any("keinen Fortschritt" in item for item in stalled.warnings)
    limited = client(page(2, raw_trade("ONLY")), max_pages=1).trade_history_preview(
        start=None, end=None, diagnostic_limit=0
    )
    assert limited.pagination_complete is False
    assert any("Sicherheitsgrenze" in item for item in limited.warnings)
    repeated = client(
        page(100, raw_trade("SAME")),
        page(100, raw_trade("SAME")),
    ).trade_history_preview(start=None, end=None, diagnostic_limit=0)
    assert repeated.pagination_complete is False
    assert any("eindeutigen Fortschritt" in item for item in repeated.warnings)


def test_trade_history_reports_changed_counts_and_provider_count_mismatch() -> None:
    changed = client(
        page(3, raw_trade("FIRST")),
        page(2, raw_trade("SECOND")),
    ).trade_history_preview(start=None, end=None, diagnostic_limit=0)
    assert changed.pagination_complete is True
    assert any("änderte sich" in item for item in changed.warnings)
    mismatch = client(page(2, raw_trade("ONLY")), max_pages=1).trade_history_preview(
        start=None, end=None, diagnostic_limit=0
    )
    assert any("Gesamtzahl" in item for item in mismatch.warnings)


@pytest.mark.parametrize(
    "result",
    [
        {"count": "invalid", "trades": {}},
        {"count": 1.5, "trades": {}},
        {"count": -1, "trades": {}},
        {"count": True, "trades": {}},
        {"count": 1, "trades": []},
    ],
)
def test_trade_history_rejects_malformed_root_payload(result: object) -> None:
    with pytest.raises(KrakenPrivateError) as raised:
        client({"error": [], "result": result}).trade_history_preview(
            start=None, end=None, diagnostic_limit=0
        )
    assert raised.value.code == "kraken_invalid_response"


def test_trade_history_rejects_non_finite_numeric_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = client(page(0))
    monkeypatch.setattr(
        history,
        "_private_post",
        lambda path, fields: {"count": Decimal("NaN"), "trades": {}},
    )
    with pytest.raises(KrakenPrivateError) as raised:
        history.trade_history_preview(start=None, end=None, diagnostic_limit=0)
    assert raised.value.code == "kraken_invalid_response"


@pytest.mark.parametrize("trade_id", ["BROKEN", ""])
def test_trade_history_counts_invalid_trade_envelope_as_malformed(
    trade_id: str,
) -> None:
    payload: object = "payload" if trade_id else raw_trade("UNUSED")[1]
    preview = client(
        {"error": [], "result": {"count": 1, "trades": {trade_id: payload}}}
    ).trade_history_preview(start=None, end=None, diagnostic_limit=0)
    assert preview.malformed_entries == 1
    assert preview.unique_total == 0
    assert preview.ready_for_import is False


@pytest.mark.parametrize(
    "changes",
    [
        {"price": "NaN"},
        {"time": "invalid"},
        {"time": "1e100"},
        {"type": "unknown"},
        {"pair": 1},
        {"ledgers": [1]},
        {"postxid": 1},
        {"leverage": 2},
    ],
)
def test_trade_history_marks_malformed_entries(changes: dict[str, object]) -> None:
    trade_id, trade = raw_trade("MALFORMED")
    trade.update(changes)
    preview = client(page(1, (trade_id, trade))).trade_history_preview(
        start=None, end=None, diagnostic_limit=0
    )
    assert preview.malformed_entries == 1
    assert preview.ready_for_import is False


def test_trade_history_accepts_string_ledgers_position_and_optional_ledgers() -> None:
    trade_id, trade = raw_trade("STRING-FIELDS", ledgers=" L1, L2, ")
    trade["postxid"] = "POSITION-1"
    requests: list[dict[str, list[str]]] = []
    preview = client(
        page(1, (trade_id, trade)), requests=requests
    ).trade_history_preview(
        start=None,
        end=None,
        diagnostic_limit=0,
        include_ledgers=False,
    )
    assert preview.records[0].ledger_ids == ("L1", "L2")
    assert preview.records[0].position_txid == "POSITION-1"
    assert "ledgers" not in requests[0]


def test_trade_history_validates_filters_and_provider_errors() -> None:
    history = client(page(0))
    with pytest.raises(ValueError, match="timezone-aware"):
        history.trade_history_preview(
            start=datetime(2021, 1, 1), end=None, diagnostic_limit=0
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        history.trade_history_preview(
            start=None, end=datetime(2021, 1, 1), diagnostic_limit=0
        )
    with pytest.raises(ValueError, match="after start"):
        history.trade_history_preview(start=START, end=START, diagnostic_limit=0)
    with pytest.raises(ValueError, match="between"):
        history.trade_history_preview(start=None, end=None, diagnostic_limit=101)

    for provider_error, code in (
        (["EGeneral:Permission denied"], "kraken_trade_history_permission_missing"),
        (["EAPI:Invalid key"], "kraken_authentication_failed"),
        (["EAPI:Rate limit exceeded"], "kraken_rate_limited"),
        (["EGeneral:synthetic provider problem"], "kraken_api_error"),
    ):
        with pytest.raises(KrakenPrivateError) as raised:
            client({"error": provider_error, "result": {}}).trade_history_preview(
                start=None, end=None, diagnostic_limit=0
            )
        assert raised.value.code == code

    with pytest.raises(KrakenPrivateError) as timeout:
        client(TimeoutError("synthetic timeout")).trade_history_preview(
            start=None, end=None, diagnostic_limit=0
        )
    assert timeout.value.code == "kraken_timeout"


def test_trade_ledger_reconciliation_all_statuses_and_sell() -> None:
    explicit = history_trade(ledger_ids=("L-BASE", "L-QUOTE"))
    matched = reconcile_trade_ledgers(
        explicit,
        (ledger("L-BASE", "DOT", "75"), ledger("L-QUOTE", "ZEUR", "-599.7599")),
    )
    assert matched.status is TradeLedgerReconciliationStatus.MATCHED
    partial = reconcile_trade_ledgers(explicit, (ledger("L-BASE", "DOT", "75"),))
    assert partial.status is TradeLedgerReconciliationStatus.PARTIAL
    assert partial.missing_ledger_ids == ("L-QUOTE",)
    pending = reconcile_trade_ledgers(explicit, ())
    assert pending.status is TradeLedgerReconciliationStatus.PENDING
    conflict = reconcile_trade_ledgers(
        explicit,
        (ledger("L-BASE", "XETH", "75"), ledger("L-QUOTE", "ZEUR", "-599.7599")),
    )
    assert conflict.status is TradeLedgerReconciliationStatus.CONFLICT
    non_trade = reconcile_trade_ledgers(
        explicit,
        (
            ledger("L-BASE", "DOT", "75", entry_type="deposit"),
            ledger("L-QUOTE", "ZEUR", "-599.7599"),
        ),
    )
    assert non_trade.status is TradeLedgerReconciliationStatus.CONFLICT

    sell = history_trade(side="sell")
    fallback = reconcile_trade_ledgers(
        sell,
        (ledger("SELL-B", "DOT", "-75"), ledger("SELL-Q", "ZEUR", "599.7599")),
    )
    assert fallback.status is TradeLedgerReconciliationStatus.MATCHED


@pytest.mark.parametrize(
    ("pair", "cost", "base_asset", "volume", "ledger_cost"),
    [
        ("DOTEUR", "599.75989", "DOT", "75.00000000", "-599.7599"),
        ("LINKEUR", "214.249600", "LINK", "10.00000000", "-214.2495"),
    ],
)
def test_trade_reconciliation_accepts_narrow_provider_quote_rounding(
    pair: str,
    cost: str,
    base_asset: str,
    volume: str,
    ledger_cost: str,
) -> None:
    trade = history_trade(
        ledger_ids=("ROUND-BASE", "ROUND-QUOTE"),
        pair=pair,
        cost=cost,
        volume=volume,
    )
    result = reconcile_trade_ledgers(
        trade,
        (
            ledger("ROUND-BASE", base_asset, volume),
            ledger("ROUND-QUOTE", "ZEUR", ledger_cost),
        ),
    )
    assert result.status is TradeLedgerReconciliationStatus.MATCHED


def test_trade_reconciliation_rejects_values_outside_narrow_tolerance() -> None:
    trade = history_trade(
        ledger_ids=("LINK-BASE", "LINK-QUOTE"),
        pair="LINKEUR",
        cost="214.249600",
        volume="10.00000000",
    )
    base = ledger("LINK-BASE", "LINK", "10.00000000")
    assert (
        reconcile_trade_ledgers(
            trade,
            (base, ledger("LINK-QUOTE", "ZEUR", "-214.2494")),
        ).status
        is TradeLedgerReconciliationStatus.CONFLICT
    )
    assert (
        reconcile_trade_ledgers(
            trade,
            (base, ledger("LINK-QUOTE", "ZEUR", "214.2495")),
        ).status
        is TradeLedgerReconciliationStatus.CONFLICT
    )
    assert (
        reconcile_trade_ledgers(
            trade,
            (base, ledger("LINK-QUOTE", "XETH", "-214.2495")),
        ).status
        is TradeLedgerReconciliationStatus.CONFLICT
    )
    assert (
        reconcile_trade_ledgers(
            trade,
            (
                ledger("LINK-BASE", "LINK", "10.00000000", refid="WRONG"),
                ledger("LINK-QUOTE", "ZEUR", "-214.2495", refid="WRONG"),
            ),
        ).status
        is TradeLedgerReconciliationStatus.CONFLICT
    )
    assert (
        reconcile_trade_ledgers(
            trade,
            (
                ledger("LINK-BASE", "LINK", "10.00000001"),
                ledger("LINK-QUOTE", "ZEUR", "-214.2495"),
            ),
        ).status
        is TradeLedgerReconciliationStatus.CONFLICT
    )


def test_trade_reconciliation_fallback_uses_same_precision_rules() -> None:
    trade = history_trade(pair="DOTEUR", cost="599.75989")
    result = reconcile_trade_ledgers(
        trade,
        (
            ledger("FALLBACK-BASE", "DOT", "75.0000000000", refid=""),
            ledger("FALLBACK-QUOTE", "ZEUR", "-599.7599", refid=""),
        ),
    )
    assert result.status is TradeLedgerReconciliationStatus.MATCHED
    wrong_reference = reconcile_trade_ledgers(
        trade,
        (
            ledger("WRONG-BASE", "DOT", "75.0000000000", refid="OTHER"),
            ledger("WRONG-QUOTE", "ZEUR", "-599.7599", refid="OTHER"),
        ),
    )
    assert wrong_reference.status is TradeLedgerReconciliationStatus.PENDING


def test_trade_reconciliation_is_conservative_for_ambiguity_and_bad_pair() -> None:
    trade = history_trade()
    ambiguous = reconcile_trade_ledgers(
        trade,
        (
            ledger("BASE-1", "DOT", "75", refid=""),
            ledger("BASE-2", "DOT", "75", refid=""),
            ledger("QUOTE", "ZEUR", "-599.7599", refid=""),
        ),
    )
    assert ambiguous.status is TradeLedgerReconciliationStatus.CONFLICT
    partial = reconcile_trade_ledgers(
        trade, (ledger("ONLY-BASE", "DOT", "75", refid=""),)
    )
    assert partial.status is TradeLedgerReconciliationStatus.PARTIAL
    distant = reconcile_trade_ledgers(
        trade,
        (
            ledger(
                "DISTANT-BASE",
                "DOT",
                "75",
                occurred_at=START + timedelta(minutes=1),
            ),
            ledger(
                "DISTANT-QUOTE",
                "ZEUR",
                "-599.7599",
                occurred_at=START + timedelta(minutes=1),
            ),
        ),
    )
    assert distant.status is TradeLedgerReconciliationStatus.PENDING
    assert (
        reconcile_trade_ledgers(trade, ()).status
        is TradeLedgerReconciliationStatus.PENDING
    )
    wrong_ref = reconcile_trade_ledgers(
        trade,
        (ledger("WRONG", "XETH", "1", refid=trade.trade_id),),
    )
    assert wrong_ref.status is TradeLedgerReconciliationStatus.CONFLICT
    bad_pair = KrakenPrivateClient._parse_trade(
        "BAD-PAIR",
        raw_trade("BAD-PAIR", pair="UNKNOWNPAIR", ledgers=("KNOWN-ID",))[1],
    )
    unresolved = reconcile_trade_ledgers(bad_pair, ())
    assert unresolved.status is TradeLedgerReconciliationStatus.CONFLICT
    assert unresolved.missing_ledger_ids == ()


def test_trade_preview_api_is_read_only_and_returns_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = client(
        page(1, raw_trade("SYNTHETIC-API")),
        {
            "error": [],
            "result": {
                "count": 2,
                "ledger": {
                    "L-EUR": {
                        "time": "1609459200.125",
                        "type": "trade",
                        "subtype": "",
                        "asset": "ZEUR",
                        "amount": "-599.7599",
                        "fee": "1.5594",
                        "refid": "SYNTHETIC-API",
                    },
                    "L-DOT": {
                        "time": "1609459200.125",
                        "type": "trade",
                        "subtype": "",
                        "asset": "DOT",
                        "amount": "75",
                        "fee": "0",
                        "refid": "SYNTHETIC-API",
                    },
                },
            },
        },
    )
    monkeypatch.setattr(kraken_live, "build_kraken_client", lambda settings: fake)
    app.dependency_overrides[get_settings] = lambda: Settings(
        environment="test", kraken_api_key="query", kraken_api_secret=SECRET
    )
    try:
        with TestClient(app) as test_client:
            response = test_client.post(
                "/api/kraken/trade-preview", json={"diagnostic_limit": 1}
            )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    body = response.json()
    assert body["connection_status"] == "CONNECTED_READ_ONLY"
    assert body["ready_for_import"] is True
    assert body["reconciliation_counts"] == {"MATCHED": 1}
    assert body["diagnostics"][0]["reconciliation_status"] == "MATCHED"
    assert body["diagnostics"][0]["transformation_payload"] == {
        "txid": "SYNTHETIC-API",
        "ordertxid": "ORDER-SYNTHETIC-API",
        "pair": "DOTEUR",
        "time": "2021-01-01T00:00:00.125000+00:00",
        "type": "buy",
        "ordertype": "market",
        "price": "7.996798666666666666666666667",
        "cost": "599.7599",
        "fee": "1.5594",
        "vol": "75",
        "ledgers": "L-EUR,L-DOT",
    }
    source = inspect.getsource(kraken_live.trade_preview)
    assert "get_session" not in source
    assert ".add(" not in source
    assert ".commit(" not in source


@pytest.mark.parametrize(
    ("request_body", "expected_code"),
    [
        ({"start": "2021-01-01T00:00:00"}, "kraken_invalid_filter"),
        (
            {
                "start": "2021-01-02T00:00:00Z",
                "end": "2021-01-01T00:00:00Z",
            },
            "kraken_invalid_period",
        ),
    ],
)
def test_trade_preview_api_rejects_invalid_periods(
    request_body: dict[str, str], expected_code: str
) -> None:
    with TestClient(app) as test_client:
        response = test_client.post("/api/kraken/trade-preview", json=request_body)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == expected_code


def test_trade_preview_api_maps_trade_permission_without_ledger_confusion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = client({"error": ["EGeneral:Permission denied"], "result": {}})
    monkeypatch.setattr(kraken_live, "build_kraken_client", lambda settings: fake)
    app.dependency_overrides[get_settings] = lambda: Settings(
        environment="test", kraken_api_key="query", kraken_api_secret=SECRET
    )
    try:
        with TestClient(app) as test_client:
            response = test_client.post("/api/kraken/trade-preview", json={})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == (
        "kraken_trade_history_permission_missing"
    )


@pytest.mark.parametrize("ledger_result", ["incomplete", "unavailable"])
def test_trade_preview_api_keeps_incomplete_reconciliation_read_only(
    monkeypatch: pytest.MonkeyPatch, ledger_result: str
) -> None:
    second: object
    if ledger_result == "incomplete":
        second = {"error": [], "result": {"count": 1, "ledger": {}}}
    else:
        second = {"error": ["EGeneral:Permission denied"], "result": {}}
    fake = client(page(1, raw_trade("SYNTHETIC-PENDING", ledgers=())), second)
    monkeypatch.setattr(kraken_live, "build_kraken_client", lambda settings: fake)
    app.dependency_overrides[get_settings] = lambda: Settings(
        environment="test", kraken_api_key="query", kraken_api_secret=SECRET
    )
    try:
        with TestClient(app) as test_client:
            response = test_client.post(
                "/api/kraken/trade-preview", json={"diagnostic_limit": 1}
            )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    body = response.json()
    assert body["ready_for_import"] is False
    assert body["reconciliation_counts"] == {"PENDING": 1}
    assert "KRAKEN_TRADE_LEDGER_RECONCILIATION_INCOMPLETE" in body["warnings"]
    expected = (
        "KRAKEN_LEDGER_RECONCILIATION_INCOMPLETE"
        if ledger_result == "incomplete"
        else "KRAKEN_LEDGER_RECONCILIATION_UNAVAILABLE:kraken_ledger_permission_missing"
    )
    assert expected in body["warnings"]
