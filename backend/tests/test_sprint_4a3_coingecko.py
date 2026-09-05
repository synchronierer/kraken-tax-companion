"""Synthetic batching, UTC evidence, pacing and rate-limit regressions."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from email.message import Message
from email.utils import format_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from test_sprint_3a_valuation import Response

from app.config.settings import Settings
from app.core.transformation import (
    ValuationMethod,
    ValuationRequirement,
    ValuationStatus,
)
from app.core.valuation import (
    DailyPrice,
    FeeTaxClassification,
    FeeTaxReviewStatus,
    PriceMethod,
    PriceObservation,
    PriceProviderError,
    ValuationDecision,
    ValuationDecisionStatus,
    daily_average,
    evidence_hash,
)
from app.database.base import Base
from app.database.session import get_session
from app.infrastructure import coingecko
from app.infrastructure.coingecko import ASSET_IDS, CoinGeckoProvider
from app.main import app
from app.services.valuation_fetch import ObservationBatch, plan_fetches, prefetch


@pytest.fixture
def database() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


@pytest.fixture
def client(database: Session) -> Iterator[TestClient]:
    def dependency() -> Iterator[Session]:
        yield database

    app.dependency_overrides[get_session] = dependency
    try:
        with TestClient(app) as value:
            yield value
    finally:
        app.dependency_overrides.clear()


START = datetime(2026, 1, 3, tzinfo=UTC)


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay


def provider(timer: FakeTime, retries: int = 2) -> CoinGeckoProvider:
    return CoinGeckoProvider(
        base_url="https://prices.invalid",
        mode="keyless",
        api_key=None,
        timeout_seconds=15,
        retries=retries,
        clock=timer.clock,
        sleeper=timer.sleep,
    )


def requirement(asset: str, day: date) -> ValuationRequirement:
    return ValuationRequirement(
        asset_code=asset,
        target_currency="EUR",
        valuation_at=datetime.combine(day, datetime.min.time(), UTC),
        method=ValuationMethod.DAILY_AVERAGE,
        status=ValuationStatus.VALUATION_REQUIRED,
        reason_code="reward_inflow",
        domain_object_type="AcquisitionLot",
        domain_object_id=uuid4(),
        transformation_run_id=uuid4(),
    )


def price(method: PriceMethod, contract: str) -> DailyPrice:
    return DailyPrice(
        asset_code="ETH",
        price_date=START.date(),
        unit_price_eur=Decimal("2"),
        method=method,
        source="synthetic",
        provider="coingecko",
        provider_contract_version=contract,
        evidence_hash="0" * 64,
        sample_count=24,
        fetched_at=START,
        status=ValuationDecisionStatus.RESOLVED,
    )


def test_sparse_production_intervals_and_direct_eur() -> None:
    requirements = [
        requirement(asset, (START + timedelta(days=7 * index)).date())
        for asset in ASSET_IDS
        for index in range(32)
        for _ in range(2)
    ]
    direct = requirement("EUR", START.date())
    direct.method = ValuationMethod.DIRECT_EUR
    plans = plan_fetches(
        requirements + [direct], [], provider(FakeTime()), refresh_prices=False
    )
    assert len(plans) == 9
    assert sum(len(p.windows) for p in plans) == 27
    assert all(len(p.required_days) == 32 for p in plans)
    assert all(len(p.missing_days) == 32 for p in plans)
    for plan in plans:
        assert plan.windows[0][0] == START
        assert plan.windows[-1][1] == START + timedelta(days=218)
        assert all(end - start <= timedelta(days=90) for start, end in plan.windows)
        assert all(
            left[1] == right[0]
            for left, right in zip(plan.windows, plan.windows[1:], strict=False)
        )


@pytest.mark.parametrize("refresh", [False, True])
@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize(
    "contract", ["market-chart-range-v1", CoinGeckoProvider.contract_version]
)
def test_price_priority_and_contract(
    refresh: bool, manual: bool, contract: str
) -> None:
    existing = price(
        PriceMethod.MANUAL_DAILY_PRICE if manual else PriceMethod.DAILY_AVERAGE_HOURLY,
        contract,
    )
    plans = plan_fetches(
        [requirement("ETH", START.date())],
        [existing],
        provider(FakeTime()),
        refresh_prices=refresh,
    )
    reused = manual or (not refresh and contract == CoinGeckoProvider.contract_version)
    assert bool(plans[0].existing_prices) == reused
    assert bool(plans[0].windows) != reused


def test_batch_boundaries_evidence_and_global_pacing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timer = FakeTime()
    source = provider(timer)
    starts: list[float] = []
    queries: list[dict[str, list[str]]] = []

    def answer(request: Request, **_: object) -> Response:
        starts.append(timer.now)
        query = parse_qs(urlsplit(request.full_url).query)
        queries.append(query)
        start, end = int(query["from"][0]), int(query["to"][0])
        return Response(
            {
                "prices": [
                    [start * 1000, 1],
                    [start * 1000, 2],
                    [(end - 1) * 1000, 3],
                    [end * 1000, 99],
                ]
            }
        )

    monkeypatch.setattr(coingecko, "urlopen", answer)
    requirements = [
        requirement(asset, (START + timedelta(days=day)).date())
        for asset in ASSET_IDS
        for day in (0, 89, 90, 180)
    ]
    plans = plan_fetches(requirements, [], source, refresh_prices=False)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        batches = prefetch(plans, source, session)
        first, last, boundary = (
            batches["ETH", (START + timedelta(days=d)).date()] for d in (0, 89, 90)
        )
        assert isinstance(first, ObservationBatch)
        assert isinstance(last, ObservationBatch)
        assert isinstance(boundary, ObservationBatch)
        assert first.evidence.id == last.evidence.id
        assert first.evidence.id != boundary.evidence.id
        assert first.evidence.requested_from == START
        assert first.evidence.requested_to == START + timedelta(days=90)
        assert first.evidence.observation_count == 2
        assert first.observations[0].price_eur == Decimal("2")
        assert boundary.observations[0].observed_at == START + timedelta(days=90)
        assert first.evidence.response_hash == evidence_hash(
            first.observations + last.observations
        )
        assert evidence_hash(first.observations) != evidence_hash(last.observations)
        timestamps = {
            o.observed_at for b in (first, last, boundary) for o in b.observations
        }
        assert len(timestamps) == 3
    assert len(starts) == 27
    assert len(timer.sleeps) == 26
    assert all(
        b - a == pytest.approx(2.1) for a, b in zip(starts, starts[1:], strict=False)
    )
    assert all(q["interval"] == ["hourly"] for q in queries)


def test_pacing_waits_only_remaining_time(monkeypatch: pytest.MonkeyPatch) -> None:
    timer = FakeTime()
    source = provider(timer)
    monkeypatch.setattr(
        coingecko, "urlopen", lambda *_a, **_k: Response({"prices": []})
    )
    source.observations("BTC", "EUR", START, START + timedelta(days=1))
    assert timer.sleeps == []
    timer.now += 1.5
    source.observations("ETH", "EUR", START, START + timedelta(days=1))
    assert timer.sleeps == pytest.approx([0.6])


@pytest.mark.parametrize(
    "retry_after,expected",
    [
        (None, [30, 60]),
        ("1", [30, 60]),
        ("120", [120, 120]),
        ("bad", [30, 60]),
        ("999999999999999999999", [30, 60]),
        ("²", [30, 60]),
    ],
)
def test_rate_limit_backoff(
    monkeypatch: pytest.MonkeyPatch,
    retry_after: str | None,
    expected: list[float],
) -> None:
    timer = FakeTime()
    source = provider(timer)
    headers = Message()
    if retry_after:
        headers["Retry-After"] = retry_after

    def limited(*_: object, **__: object) -> None:
        raise HTTPError("https://prices.invalid", 429, "limited", headers, None)

    monkeypatch.setattr(coingecko, "urlopen", limited)
    with pytest.raises(PriceProviderError) as caught:
        source.observations("ETH", "EUR", START, START + timedelta(days=1))
    assert caught.value.code == "valuation_provider_rate_limited"
    assert caught.value.temporary
    assert timer.sleeps == expected


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError(),
        URLError("offline"),
        HTTPError("https://prices.invalid", 503, "unavailable", Message(), None),
    ],
)
def test_normal_errors_keep_short_backoff(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    timer = FakeTime()
    source = provider(timer)

    def fail(*_: object, **__: object) -> None:
        raise error

    monkeypatch.setattr(coingecko, "urlopen", fail)
    with pytest.raises(PriceProviderError) as caught:
        source.observations("ETH", "EUR", START, START + timedelta(days=1))
    assert caught.value.temporary
    assert sum(timer.sleeps) == pytest.approx(4.2)
    assert max(timer.sleeps) < 30


def test_retry_after_http_dates() -> None:
    future = format_datetime(datetime.now(UTC) + timedelta(seconds=120), usegmt=True)
    assert 118 <= CoinGeckoProvider._retry_delay(0, future) <= 120
    past = format_datetime(START, usegmt=True)
    assert CoinGeckoProvider._retry_delay(0, past) == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("coingecko_min_interval_seconds", 0),
        ("coingecko_min_interval_seconds", float("inf")),
        ("coingecko_rate_limit_retry_base_seconds", 29),
        ("coingecko_rate_limit_retry_base_seconds", float("nan")),
    ],
)
def test_configuration_bounds(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: value})


def test_incomplete_current_and_adjacent_utc_days() -> None:
    observations = tuple(
        PriceObservation(observed_at=START + timedelta(hours=h), price_eur=Decimal("2"))
        for h in (-1, 0, 1, 24)
    )
    unit, status, reason = daily_average(
        observations, START.date(), now=START + timedelta(days=2)
    )
    assert unit == Decimal("2")
    assert status == ValuationDecisionStatus.REVIEW_REQUIRED
    assert reason == "valuation_incomplete_daily_coverage"
    with pytest.raises(PriceProviderError, match="UTC-Tag") as caught:
        daily_average(observations, START.date(), now=START + timedelta(hours=2))
    assert caught.value.code == "valuation_future_date"


def test_representative_reward_run(
    client: TestClient, database: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = price(PriceMethod.DAILY_AVERAGE_HOURLY, "market-chart-range-v1")
    database.add(old)
    database.commit()
    calls: list[tuple[str, datetime, datetime]] = []

    def observations(
        self: CoinGeckoProvider,
        asset: str,
        currency: str,
        start: datetime,
        end: datetime,
    ) -> tuple[PriceObservation, ...]:
        assert currency == "EUR"
        calls.append((asset, start, end))
        return tuple(
            PriceObservation(
                observed_at=start + timedelta(hours=h),
                price_eur=Decimal("2.1234567890123456789"),
            )
            for h in range((end - start).days * 24)
        )

    monkeypatch.setattr(CoinGeckoProvider, "observations", observations)
    rows = ["txid,time,type,asset,amount,fee,subtype"]
    for asset in ("ETH", "BTC"):
        for month in (1, 2, 5, 8):
            for index, fee in enumerate(("0.25", "0")):
                rows.append(
                    f"{asset}{month}{index},2026-{month:02d}-03 03:04:05,"
                    f"earn,{asset},1,{fee},reward"
                )
    response = client.post(
        "/api/imports/kraken?transform=true",
        files={"file": ("rewards.csv", "\n".join(rows).encode())},
    )
    assert response.status_code == 200
    response = client.post("/api/valuations")
    assert response.status_code == 200
    assert response.json()["resolved"] == 16
    assert response.json()["reviews"] == 0
    assert len(calls) == 6
    prices = list(
        database.scalars(
            select(DailyPrice).where(
                DailyPrice.provider_contract_version
                == CoinGeckoProvider.contract_version
            )
        )
    )
    decisions = list(database.scalars(select(ValuationDecision)))
    assert len(prices) == 8
    assert len(decisions) == 16
    assert len({p.provider_evidence_id for p in prices}) == 6
    assert len({(p.asset_code, p.price_date) for p in prices}) == 8
    unit = Decimal("2.1234567890123456789")
    for daily in prices:
        assert daily.sample_count == 24
        assert daily.minimum_price_eur == daily.maximum_price_eur == unit
        assert daily.earliest_sample_at == datetime.combine(
            daily.price_date, datetime.min.time(), UTC
        )
        assert daily.latest_sample_at == daily.earliest_sample_at + timedelta(hours=23)
        own = tuple(
            PriceObservation(
                observed_at=daily.earliest_sample_at + timedelta(hours=h),
                price_eur=unit,
            )
            for h in range(24)
        )
        assert daily.evidence_hash == evidence_hash(own)
        assert sum(d.provider_object_id == daily.id for d in decisions) == 2
    for decision in decisions:
        assert decision.gross_quantity == Decimal("1")
        assert decision.fee_quantity in {Decimal("0"), Decimal("0.25")}
        assert decision.net_quantity == Decimal("1") - decision.fee_quantity
        assert decision.gross_income_eur == unit
        assert decision.fee_value_eur == decision.fee_quantity * unit
        assert decision.net_acquisition_value_eur == decision.net_quantity * unit
        assert decision.valuation_basis == "staking_reward_components_v2"
        assert decision.fee_tax_classification == (
            FeeTaxClassification.WERBUNGSKOSTEN_CANDIDATE
            if decision.fee_quantity
            else FeeTaxClassification.NOT_APPLICABLE
        )
        assert decision.fee_tax_review_status == (
            FeeTaxReviewStatus.REVIEW_REQUIRED
            if decision.fee_quantity
            else FeeTaxReviewStatus.NOT_REQUIRED
        )
    historical = database.get(DailyPrice, old.id)
    assert historical is not None
    assert historical.provider_contract_version == "market-chart-range-v1"
    assert client.post("/api/valuations").json()["checked"] == 0
    assert len(calls) == 6


def test_manual_wins_over_newer_automatic() -> None:
    automatic = price(
        PriceMethod.DAILY_AVERAGE_HOURLY, CoinGeckoProvider.contract_version
    )
    automatic.version = 10
    manual = price(PriceMethod.MANUAL_DAILY_PRICE, "manual-v1")
    plan = plan_fetches(
        [requirement("ETH", START.date())],
        [automatic, manual],
        provider(FakeTime()),
        refresh_prices=True,
    )[0]
    assert plan.existing_prices == (manual,)
    assert plan.windows == ()


@pytest.mark.parametrize("recover", [False, True])
def test_http_attempt_evidence(
    database: Session, monkeypatch: pytest.MonkeyPatch, recover: bool
) -> None:
    from app.core.valuation import ProviderEvidence

    timer = FakeTime()
    source = provider(timer)
    calls = 0
    audits: list[tuple[str, dict[str, object]]] = []

    def answer(*_: object, **__: object) -> Response:
        nonlocal calls
        calls += 1
        if calls == 1 or not recover:
            raise HTTPError("https://prices.invalid", 429, "limited", Message(), None)
        return Response({"prices": [[int(START.timestamp()) * 1000, 2]]})

    monkeypatch.setattr(coingecko, "urlopen", answer)
    plan = plan_fetches(
        [requirement("ETH", START.date())], [], source, refresh_prices=False
    )
    result = prefetch(
        plan,
        source,
        database,
        audit=lambda event, metadata: audits.append((event, metadata)),
    )["ETH", START.date()]
    evidence = list(database.scalars(select(ProviderEvidence)))
    assert len(source.attempts) == (2 if recover else 3)
    assert [a.http_status for a in source.attempts] == (
        [429, 200] if recover else [429] * 3
    )
    stored_attempts = [
        metadata
        for event, metadata in audits
        if event == "valuation.provider_evidence_stored"
    ]
    assert len(stored_attempts) == len(source.attempts)
    assert len(evidence) == (2 if recover else 1)
    assert [e.http_status for e in evidence] == ([429, 200] if recover else [429])
    assert all(e.requested_from == START for e in evidence)
    assert all(e.requested_to == START + timedelta(days=1) for e in evidence)
    if recover:
        assert isinstance(result, ObservationBatch)
        assert result.evidence.id == evidence[-1].id
        assert result.evidence.response_hash == evidence_hash(result.observations)
    else:
        assert isinstance(result, PriceProviderError)
        assert result.code == "valuation_provider_rate_limited"
    assert evidence[0].observations == []
    assert evidence[0].observation_count == 0
    assert evidence[0].response_hash == evidence_hash(())
    assert evidence[0].earliest_observed_at is None


@pytest.mark.parametrize("minimum,base", [(0, 30), (301, 30), (2.1, 29), (2.1, 3601)])
def test_provider_configuration_bounds(minimum: float, base: float) -> None:
    with pytest.raises(ValueError):
        CoinGeckoProvider(
            base_url="https://prices.invalid",
            mode="keyless",
            api_key=None,
            timeout_seconds=15,
            min_interval_seconds=minimum,
            rate_limit_retry_base_seconds=base,
        )
