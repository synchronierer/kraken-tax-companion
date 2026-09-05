from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from test_kraken_live_preview import SECRET, FakeResponse, entry, preview
from test_kraken_sync import NOW, Client, database, service
from test_kraken_sync import preview as sync_preview

from app.api.kraken_live import build_kraken_client
from app.config.settings import Settings
from app.core.incremental_sync import IncrementalSyncRun, SyncStatus
from app.infrastructure.kraken_private import KrakenPrivateClient, KrakenPrivateError
from app.services.kraken_sync import KrakenSyncError


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_pagination_and_confirmation_share_pacing_and_one_client() -> None:
    timer = FakeTime()
    starts: list[float] = []
    pages = iter(
        [
            {"count": 2, "ledger": {"L1": entry(entry_type="staking")}},
            {"count": 2, "ledger": {"L2": entry(entry_type="staking")}},
        ]
        * 2
    )

    def opener(_: Request, __: float) -> FakeResponse:
        starts.append(timer.now)
        return FakeResponse({"error": [], "result": next(pages)})

    client = KrakenPrivateClient(
        api_key="synthetic",
        api_secret=SECRET,
        opener=opener,
        clock=timer.clock,
        sleeper=timer.sleep,
    )
    factories: list[KrakenPrivateClient] = []

    def factory() -> KrakenPrivateClient:
        factories.append(client)
        return client

    db = database()
    sync = service(db, Client(sync_preview()))
    sync.client_factory = factory
    result = sync.run_sync()
    assert result.status is SyncStatus.COMPLETED
    assert result.fetched_pages == 4
    assert factories == [client]
    assert starts == [0, 9, 18, 27]
    assert timer.sleeps == [9, 9, 9]
    db.close()


def test_elapsed_time_and_non_ledger_requests() -> None:
    timer = FakeTime()
    client = KrakenPrivateClient(
        api_key="synthetic",
        api_secret=SECRET,
        opener=lambda *_: FakeResponse(
            {"error": [], "result": {"count": 0, "ledger": {}}}
        ),
        clock=timer.clock,
        sleeper=timer.sleep,
        ledger_min_interval_seconds=12,
    )
    preview(client)
    assert timer.sleeps == []
    timer.now += 5
    preview(client)
    assert timer.sleeps == [7]
    timer.now += 20
    preview(client)
    client._private_post("/0/public/Time", {})
    client._private_post("/0/public/Time", {})
    assert timer.sleeps == [7]


@pytest.mark.parametrize("kind", ["http", "json"])
@pytest.mark.parametrize("exhausted", [False, True])
def test_rate_limit_retries(kind: str, exhausted: bool) -> None:
    timer = FakeTime()
    starts: list[float] = []

    def opener(_: Request, __: float) -> FakeResponse:
        starts.append(timer.now)
        if len(starts) <= 2 or exhausted:
            if kind == "http":
                raise HTTPError("synthetic", 429, "rate", {}, None)
            return FakeResponse({"error": ["EAPI:Rate limit exceeded"]})
        return FakeResponse({"error": [], "result": {"count": 0, "ledger": {}}})

    client = KrakenPrivateClient(
        api_key="synthetic",
        api_secret=SECRET,
        opener=opener,
        clock=timer.clock,
        sleeper=timer.sleep,
    )
    if exhausted:
        with pytest.raises(KrakenPrivateError) as caught:
            preview(client)
        assert caught.value.code == "kraken_rate_limited"
    else:
        assert preview(client).ready_for_import
    assert timer.sleeps == [30, 60]
    assert starts == [0, 30, 90]


@pytest.mark.parametrize(
    "error", [TimeoutError(), HTTPError("synthetic", 503, "down", {}, None)]
)
def test_temporary_retry_stays_short(error: Exception) -> None:
    timer = FakeTime()
    calls = 0

    def opener(_: Request, __: float) -> FakeResponse:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise error
        return FakeResponse({"error": [], "result": {"count": 0, "ledger": {}}})

    client = KrakenPrivateClient(
        api_key="synthetic",
        api_secret=SECRET,
        opener=opener,
        clock=timer.clock,
        sleeper=timer.sleep,
    )
    preview(client)
    assert timer.sleeps == [1, 8, 2, 7]


@pytest.mark.parametrize("header,expected", [("75", 75), ("2", 30), ("invalid", 30)])
def test_retry_after_respects_safe_minimum(header: str, expected: float) -> None:
    timer = FakeTime()
    calls = 0

    def opener(_: Request, __: float) -> FakeResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPError("synthetic", 429, "rate", {"Retry-After": header}, None)
        return FakeResponse({"error": [], "result": {"count": 0, "ledger": {}}})

    preview(
        KrakenPrivateClient(
            api_key="synthetic",
            api_secret=SECRET,
            opener=opener,
            clock=timer.clock,
            sleeper=timer.sleep,
        )
    )
    assert timer.sleeps == [expected]


def test_retry_after_date_and_invalid_values() -> None:
    future = format_datetime(NOW + timedelta(seconds=120))
    with patch("app.infrastructure.kraken_private.datetime") as wall_clock:
        wall_clock.now.return_value = NOW
        assert KrakenPrivateClient._retry_after(future) == 120
    for value in [None, "garbage", "nan", "inf", "-1", "Thu, 01 Jan 1970 00:00:00 GMT"]:
        assert KrakenPrivateClient._retry_after(value) == 0


@pytest.mark.parametrize(
    "field,values",
    [
        (
            "kraken_ledger_min_interval_seconds",
            [0, -1, 301, float("nan"), float("inf")],
        ),
        (
            "kraken_rate_limit_retry_base_seconds",
            [0, 29, 3601, float("nan"), float("inf")],
        ),
    ],
)
def test_settings_and_client_reject_invalid_intervals(
    field: str, values: list[float]
) -> None:
    for value in values:
        with pytest.raises(ValidationError):
            Settings(**{field: value})
        with pytest.raises(KrakenPrivateError) as caught:
            KrakenPrivateClient(
                api_key="synthetic",
                api_secret=SECRET,
                **{field.removeprefix("kraken_"): value},
            )
        assert caught.value.code == "kraken_configuration_invalid"


def test_composition_passes_configured_intervals() -> None:
    client = build_kraken_client(
        Settings(
            kraken_api_key="synthetic",
            kraken_api_secret=SECRET,
            kraken_ledger_min_interval_seconds=15,
            kraken_rate_limit_retry_base_seconds=45,
        )
    )
    assert client._ledger_interval == 15
    assert client._retry_delay(KrakenPrivateError("kraken_rate_limited", ""), 1) == 90


def test_failed_run_is_preserved_and_does_not_block_or_checkpoint() -> None:
    db = database()
    failed_service = service(
        db, Client(KrakenPrivateError("kraken_rate_limited", "rate"))
    )
    with pytest.raises(KrakenSyncError):
        failed_service.run_sync()
    failed = db.scalar(select(IncrementalSyncRun))
    assert failed is not None
    before = (
        failed.status,
        failed.requested_from,
        failed.requested_to,
        failed.error_code,
        failed.ended_at,
    )
    completed = service(
        db, Client(sync_preview()), now=NOW + timedelta(hours=1)
    ).run_sync()
    assert completed.status is SyncStatus.COMPLETED
    assert completed.previous_success_id is None
    assert completed.requested_from == datetime(2026, 1, 1, tzinfo=UTC)
    db.refresh(failed)
    assert before == (
        failed.status,
        failed.requested_from,
        failed.requested_to,
        failed.error_code,
        failed.ended_at,
    )
    assert failed.status is SyncStatus.FAILED
    db.close()
