import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.request import Request
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.workflows import reusable_transformation_run
from app.api.workflows import valuations as run_valuations
from app.config.settings import get_settings
from app.core.transformation import (
    TransformationRun,
    TransformationRunSession,
    TransformationStatus,
    ValuationMethod,
    ValuationRequirement,
    ValuationStatus,
)
from app.core.valuation import (
    METHOD_VERSION,
    DailyPrice,
    PriceMethod,
    PriceObservation,
    PriceProviderError,
    ProviderEvidence,
    ValuationDecision,
    ValuationDecisionStatus,
    ValuationRun,
    ValuationRunStatus,
    calculate_eur_value,
    daily_average,
    display_cents,
    evidence_hash,
    transition_valuation_run,
    utc_day_bounds,
)
from app.database.base import Base
from app.database.session import get_session
from app.infrastructure import coingecko
from app.infrastructure.coingecko import CoinGeckoProvider
from app.main import app

NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)
DAY = date(2026, 7, 29)


def observations(count: int) -> tuple[PriceObservation, ...]:
    start, _ = utc_day_bounds(DAY)
    return tuple(
        PriceObservation(
            observed_at=start + timedelta(hours=index), price_eur=Decimal(index + 1)
        )
        for index in range(count)
    )


def test_calculation_contract() -> None:
    start, end = utc_day_bounds(DAY)
    assert start == datetime(2026, 7, 29, tzinfo=UTC)
    assert end.date() == DAY
    average, status, reason = daily_average(observations(24), DAY, now=NOW)
    assert average == Decimal("12.5")
    assert status is ValuationDecisionStatus.RESOLVED
    assert reason == "valuation_resolved"
    assert daily_average(observations(20), DAY, now=NOW)[1] is status
    assert (
        daily_average(observations(19), DAY, now=NOW)[1]
        is ValuationDecisionStatus.REVIEW_REQUIRED
    )
    duplicated = observations(20) + (observations(20)[0],)
    assert daily_average(duplicated, DAY, now=NOW)[0] == Decimal("10.5")
    assert calculate_eur_value(Decimal("0.123456789"), Decimal("2.5")) == Decimal(
        "0.3086419725"
    )
    assert display_cents(Decimal("1.005")) == Decimal("1.01")
    assert len(evidence_hash(observations(2))) == 64
    with pytest.raises(PriceProviderError, match="abgeschlossen"):
        daily_average(observations(2), NOW.date(), now=NOW)
    with pytest.raises(PriceProviderError, match="Keine"):
        daily_average((), DAY, now=NOW)
    with pytest.raises((TypeError, ValueError)):
        calculate_eur_value(Decimal("0"), Decimal("1"))
    with pytest.raises((TypeError, ValueError)):
        PriceObservation(observed_at=NOW, price_eur=Decimal("-1"))


def test_evidence_and_daily_price_validation() -> None:
    normalized = [{"observed_at": NOW.isoformat(), "price_eur": "1"}]
    evidence = ProviderEvidence(
        provider="coingecko",
        provider_contract_version="v1",
        provider_asset_id="bitcoin",
        target_currency="eur",
        requested_from=NOW - timedelta(days=1),
        requested_to=NOW,
        fetched_at=NOW,
        http_status=200,
        response_hash="a" * 64,
        observation_count=1,
        observations=normalized,
    )
    assert evidence.target_currency == "EUR"
    with pytest.raises(ValueError):
        ProviderEvidence(
            provider="x",
            provider_contract_version="v",
            provider_asset_id="x",
            target_currency="EUR",
            requested_from=NOW,
            requested_to=NOW,
            fetched_at=NOW,
            http_status=99,
            response_hash="a" * 64,
            observation_count=0,
            observations=[],
        )
    with pytest.raises(ValueError):
        ProviderEvidence(
            provider="x",
            provider_contract_version="v",
            provider_asset_id="x",
            target_currency="EUR",
            requested_from=NOW - timedelta(days=1),
            requested_to=NOW,
            fetched_at=NOW,
            http_status=200,
            response_hash="a" * 64,
            observation_count=2,
            observations=[],
        )
    price = DailyPrice(
        asset_code="btc",
        price_date=DAY,
        unit_price_eur=Decimal("1"),
        method=PriceMethod.MANUAL_DAILY_PRICE,
        source="Beleg",
        provider="manual",
        provider_contract_version="v1",
        evidence_hash="a" * 64,
        sample_count=1,
        fetched_at=NOW,
        status=ValuationDecisionStatus.RESOLVED,
    )
    assert price.asset_code == "BTC"
    with pytest.raises(ValueError):
        DailyPrice(
            asset_code="BTC",
            price_date=DAY,
            unit_price_eur=Decimal("1"),
            method=PriceMethod.MANUAL_DAILY_PRICE,
            source="Beleg",
            provider="manual",
            provider_contract_version="v1",
            evidence_hash="a" * 64,
            sample_count=-1,
            fetched_at=NOW,
            status=ValuationDecisionStatus.RESOLVED,
        )
    run = ValuationRun(
        provider="manual",
        correlation_id=uuid4(),
        started_at=NOW,
        ended_at=NOW,
        status=ValuationRunStatus.COMPLETED,
    )
    assert run.ended_at is NOW
    with pytest.raises(ValueError):
        ValuationRun(provider=" ", correlation_id=uuid4(), started_at=NOW)
    run = ValuationRun(provider="manual", correlation_id=uuid4(), started_at=NOW)
    transition_valuation_run(run, ValuationRunStatus.FETCHING, NOW)
    transition_valuation_run(run, ValuationRunStatus.APPLYING, NOW)
    transition_valuation_run(run, ValuationRunStatus.COMPLETED_WITH_REVIEW, NOW)
    assert run.ended_at == NOW
    with pytest.raises(ValueError, match="not allowed"):
        transition_valuation_run(run, ValuationRunStatus.FAILED, NOW)
    failed = ValuationRun(provider="manual", correlation_id=uuid4(), started_at=NOW)
    transition_valuation_run(
        failed,
        ValuationRunStatus.FAILED,
        NOW,
        error_summary="synthetic repository failure",
    )
    assert failed.error_count == 1
    assert failed.error_summary == "synthetic repository failure"


def test_decision_validates_financial_values_and_utc() -> None:
    decision = ValuationDecision(
        valuation_requirement_id=uuid4(),
        valuation_run_id=uuid4(),
        domain_object_type="acquisition_lot",
        domain_object_id=uuid4(),
        asset_code="BTC",
        quantity=Decimal("0.1"),
        valuation_at=NOW,
        price_date=DAY,
        method=PriceMethod.NATIVE_EUR,
        unit_price_eur=Decimal("1"),
        eur_value=Decimal("0.1"),
        price_source="Kraken",
        provider="kraken",
        provider_object_id=None,
        provider_contract_version="v1",
        method_version="v1",
        sample_count=1,
        fetched_at=NOW,
        decided_at=NOW,
        status=ValuationDecisionStatus.RESOLVED,
        reason_code="valuation_native_eur",
    )
    assert decision.eur_value == Decimal("0.1")
    with pytest.raises(ValueError):
        replace(decision, id=uuid4(), quantity=Decimal("0"))


class Response:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def provider(mode: str = "keyless", key: str | None = None) -> CoinGeckoProvider:
    return CoinGeckoProvider(
        base_url="https://prices.invalid",
        mode=mode,
        api_key=key,
        timeout_seconds=1,
        retries=1,
    )


def test_coingecko_modes_and_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "api-key" not in provider()._headers()
    assert (
        provider("demo", "demo-secret")._headers()["x-cg-demo-api-key"] == "demo-secret"
    )
    assert provider("pro", "pro-secret")._headers()["x-cg-pro-api-key"] == "pro-secret"
    with pytest.raises(ValueError):
        provider("bad")
    with pytest.raises(ValueError):
        provider("demo")
    with pytest.raises(ValueError, match="must not be negative"):
        CoinGeckoProvider(
            base_url="https://prices.invalid",
            mode="keyless",
            api_key=None,
            timeout_seconds=1,
            retries=-1,
        )
    with pytest.raises(PriceProviderError) as disabled:
        provider("disabled").observations("BTC", "EUR", NOW, NOW + timedelta(hours=1))
    assert disabled.value.code == "valuation_provider_disabled"
    with pytest.raises(PriceProviderError) as missing:
        provider().observations("NOPE", "EUR", NOW, NOW + timedelta(hours=1))
    assert missing.value.code == "valuation_asset_mapping_missing"
    assert provider().asset_id("BTC") == "bitcoin"
    with pytest.raises(PriceProviderError):
        provider().asset_id("NOPE")

    millis = int(NOW.timestamp()) * 1000
    monkeypatch.setattr(
        coingecko,
        "urlopen",
        lambda *_a, **_k: Response(
            {
                "prices": [
                    [millis + 1000, "2.000000000000000001"],
                    [millis, 1],
                    [millis, "3"],
                    [millis - 1, "9"],
                ]
            }
        ),
    )
    result = provider().observations("BTC", "EUR", NOW, NOW + timedelta(hours=1))
    assert [item.price_eur for item in result] == [
        Decimal("3"),
        Decimal("2.000000000000000001"),
    ]


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "valuation_provider_unavailable"),
        (401, "valuation_provider_unauthorized"),
        (403, "valuation_provider_unauthorized"),
        (404, "valuation_no_price_data"),
    ],
)
def test_coingecko_permanent_http_errors(
    monkeypatch: pytest.MonkeyPatch, status: int, code: str
) -> None:
    def fail(*_a: object, **_k: object) -> None:
        raise HTTPError("safe", status, "safe", {}, BytesIO())

    monkeypatch.setattr(coingecko, "urlopen", fail)
    with pytest.raises(PriceProviderError) as caught:
        provider().observations("BTC", "EUR", NOW, NOW + timedelta(hours=1))
    assert caught.value.code == code


def test_coingecko_retries_and_invalid_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fail(*_a: object, **_k: object) -> None:
        nonlocal calls
        calls += 1
        raise HTTPError("safe", 429, "safe", {"Retry-After": "0"}, BytesIO())

    monkeypatch.setattr(coingecko, "urlopen", fail)
    monkeypatch.setattr(coingecko.time, "sleep", lambda _: None)
    with pytest.raises(PriceProviderError) as caught:
        provider("demo", "synthetic-secret").observations(
            "ETH", "EUR", NOW, NOW + timedelta(hours=1)
        )
    assert calls == 2 and caught.value.temporary
    assert "synthetic-secret" not in str(caught.value)

    attempts = 0
    millis = int(NOW.timestamp()) * 1000

    def succeed_after_temporary_error(*_a: object, **_k: object) -> Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise HTTPError("safe", 500, "safe", {}, BytesIO())
        return Response({"prices": [[millis, "123.456"]]})

    monkeypatch.setattr(coingecko, "urlopen", succeed_after_temporary_error)
    recovered = provider().observations("BTC", "EUR", NOW, NOW + timedelta(hours=1))
    assert attempts == 2
    assert recovered[0].price_eur == Decimal("123.456")

    monkeypatch.setattr(coingecko, "urlopen", lambda *_a, **_k: Response([]))
    with pytest.raises(PriceProviderError):
        provider().observations("BTC", "EUR", NOW, NOW + timedelta(hours=1))


def test_coingecko_windows_timeout_and_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []

    def answer(request: Request, **_: object) -> Response:
        requested.append(request.full_url)
        return Response({"prices": []})

    monkeypatch.setattr(coingecko, "urlopen", answer)
    result = provider().observations("BTC", "EUR", NOW, NOW + timedelta(days=181))
    assert result == ()
    assert len(requested) == 3
    assert CoinGeckoProvider._retry_delay(2, None) == 4
    assert CoinGeckoProvider._retry_delay(0, "31") == 30
    assert CoinGeckoProvider._retry_delay(0, "not-a-date") == 1
    future_naive = (datetime.now(UTC) + timedelta(minutes=1)).strftime(
        "%a, %d %b %Y %H:%M:%S"
    )
    assert 0 < CoinGeckoProvider._retry_delay(0, future_naive) <= 30

    calls = 0

    def timeout(*_: object, **__: object) -> None:
        nonlocal calls
        calls += 1
        raise TimeoutError

    monkeypatch.setattr(coingecko, "urlopen", timeout)
    monkeypatch.setattr(coingecko.time, "sleep", lambda _: None)
    with pytest.raises(PriceProviderError) as caught:
        provider().observations("BTC", "EUR", NOW, NOW + timedelta(hours=1))
    assert calls == 2
    assert caught.value.code == "valuation_provider_timeout"
    with pytest.raises(ValueError, match="positive duration"):
        provider().observations("BTC", "EUR", NOW, NOW)
    monkeypatch.setattr(
        coingecko,
        "urlopen",
        lambda *_a, **_k: Response({"prices": [["1.5", "1"]]}),
    )
    with pytest.raises(PriceProviderError):
        provider().observations("BTC", "EUR", NOW, NOW + timedelta(hours=1))
    monkeypatch.setattr(
        coingecko,
        "urlopen",
        lambda *_a, **_k: Response({"not_prices": []}),
    )
    with pytest.raises(PriceProviderError):
        provider().observations("BTC", "EUR", NOW, NOW + timedelta(hours=1))
    monkeypatch.setattr(
        coingecko, "urlopen", lambda *_a, **_k: Response({"prices": [["bad"]]})
    )
    with pytest.raises(PriceProviderError):
        provider().observations("BTC", "EUR", NOW, NOW + timedelta(hours=1))
    monkeypatch.setattr(
        coingecko, "urlopen", lambda *_a, **_k: (_ for _ in ()).throw(URLError("safe"))
    )
    with pytest.raises(PriceProviderError):
        provider().observations("BTC", "EUR", NOW, NOW + timedelta(hours=1))


@pytest.fixture
def client() -> TestClient:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)

    def dependency() -> object:
        with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = dependency
    with TestClient(app) as value:
        yield value
    app.dependency_overrides.clear()


def test_empty_api_and_errors(client: TestClient) -> None:
    dashboard = client.get("/api/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.json() == {
        "imports": 0,
        "raw_records": 0,
        "transformation_runs": 0,
        "rewards": 0,
        "trades": 0,
        "acquisitions": 0,
        "disposals": 0,
        "open_valuations": 0,
        "resolved_valuations": 0,
        "review_cases": 0,
        "last_import_at": None,
        "last_valuation_at": None,
        "price_source": {"mode": "disabled", "available": False},
    }
    for path in (
        "/api/imports",
        "/api/transformations",
        "/api/events",
        "/api/valuation-requirements",
        "/api/prices",
        "/api/valuations",
        "/api/reviews",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json()["total"] == 0
    assert client.get("/api/system/status").json()["api_key_configured"] is False
    random = uuid4()
    for path in (
        f"/api/imports/{random}",
        f"/api/transformations/{random}",
        f"/api/events/trade/{random}",
        f"/api/prices/{random}",
        f"/api/valuations/{random}",
        f"/api/reviews/{random}",
    ):
        assert client.get(path).status_code == 404
    assert (
        client.post(
            "/api/imports/kraken", files={"file": ("bad.txt", b"x")}
        ).status_code
        == 422
    )


def test_upload_limits_parser_errors_and_validation_contract(
    client: TestClient,
) -> None:
    settings = get_settings()
    previous_limit = settings.max_upload_bytes
    settings.max_upload_bytes = 4
    try:
        too_large = client.post(
            "/api/imports/kraken", files={"file": ("large.csv", b"12345")}
        )
    finally:
        settings.max_upload_bytes = previous_limit
    assert too_large.status_code == 413
    assert too_large.json()["detail"]["code"] == "import_file_too_large"

    malformed = client.post(
        "/api/imports/kraken", files={"file": ("invalid.csv", b"not,a,kraken,csv")}
    )
    assert malformed.status_code == 422
    assert malformed.json()["detail"]["code"] == "import_validation_failed"

    invalid_query = client.get("/api/imports?limit=0")
    assert invalid_query.status_code == 422
    assert invalid_query.json()["detail"]["code"] == "request_validation_failed"
    assert invalid_query.json()["detail"]["errors"][0]["location"][-1] == "limit"


def test_unexpected_dashboard_error_is_structured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.database.dashboard_queries import SqlAlchemyDashboardQueries

    def fail(_: SqlAlchemyDashboardQueries) -> None:
        raise RuntimeError("synthetic infrastructure failure")

    monkeypatch.setattr(SqlAlchemyDashboardQueries, "counts", fail)
    del client
    with TestClient(app, raise_server_exceptions=False) as safe_client:
        response = safe_client.get("/api/dashboard")
    assert response.status_code == 500
    assert response.json() == {
        "detail": {
            "code": "internal_server_error",
            "message": "Das Backend hat die Anfrage nicht verarbeiten können.",
        }
    }
    assert "synthetic infrastructure failure" not in response.text


def test_sprint_3a_domain_classes_are_imperatively_mapped() -> None:
    from app.core.entities import ImportSession, RawImportRecord
    from app.core.transformation import (
        AcquisitionLot,
        DisposalEvent,
        FeeEvent,
        TradeExecution,
        TransformationRun,
        ValuationRequirement,
    )
    from app.core.valuation import DailyPrice, ValuationDecision, ValuationRun

    for model in (
        ImportSession,
        RawImportRecord,
        TransformationRun,
        AcquisitionLot,
        DisposalEvent,
        TradeExecution,
        FeeEvent,
        ValuationRequirement,
        ValuationRun,
        DailyPrice,
        ValuationDecision,
    ):
        assert sqlalchemy_inspect(model).mapper.class_ is model


def test_manual_price_api(client: TestClient) -> None:
    payload = {
        "asset": "BTC",
        "date": "2026-07-01",
        "price_eur": "123.45",
        "source": "synthetischer Beleg",
        "reason": "Test",
    }
    created = client.post("/api/prices/manual", json=payload)
    assert created.status_code == 200
    created2 = client.post("/api/prices/manual", json=payload)
    assert created2.json()["version"] == 1
    assert created2.json()["duplicate"] is True
    corrected = client.post(
        "/api/prices/manual", json={**payload, "price_eur": "123.46"}
    )
    assert corrected.json()["version"] == 2
    item = client.get(f"/api/prices/{corrected.json()['id']}").json()
    assert item["price_eur"] == "123.46"
    previous = client.get(f"/api/prices/{created.json()['id']}").json()
    assert previous["effective_status"] == "superseded"
    assert previous["superseded_by_id"] == corrected.json()["id"]
    csv_data = (
        "\ufeffasset,date,price_eur,source,reason\r\n"
        "ETH,2026-07-02,2.5,Beleg,Test\r\n"
        "BTC,2026-07-03,3.5,Beleg,Test\r\n"
    )
    assert (
        client.post(
            "/api/prices/manual/csv", files={"file": ("prices.csv", csv_data)}
        ).json()["count"]
        == 2
    )
    assert (
        client.post(
            "/api/prices/manual/csv", files={"file": ("prices.csv", "wrong\nx")}
        ).status_code
        == 422
    )
    before = client.get("/api/prices").json()["total"]
    atomic = (
        "asset,date,price_eur,source,reason\n"
        "ETH,2026-07-04,2.5,Beleg,Test\n"
        "BTC,2026-07-05,0,Beleg,Test\n"
    )
    invalid = client.post(
        "/api/prices/manual/csv", files={"file": ("prices.csv", atomic)}
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["line"] == 3
    assert client.get("/api/prices").json()["total"] == before


@pytest.mark.parametrize(
    ("filename", "content", "status", "code", "field"),
    [
        ("prices.txt", b"x", 422, "manual_csv_invalid_file_type", None),
        ("prices.csv", b"x" * 512_001, 413, "manual_csv_too_large", None),
        (
            "prices.csv",
            b"asset,date,price_eur,source,reason\nBTC,2099-01-01,1,S,R\n",
            422,
            "valuation_future_date",
            "date",
        ),
        (
            "prices.csv",
            b"asset,date,price_eur,source,reason\nBTC,2026-01-01,1,,R\n",
            422,
            "manual_csv_invalid",
            "source",
        ),
    ],
)
def test_manual_csv_boundary_errors_are_structured(
    client: TestClient,
    filename: str,
    content: bytes,
    status: int,
    code: str,
    field: str | None,
) -> None:
    response = client.post(
        "/api/prices/manual/csv", files={"file": (filename, content)}
    )
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    if field is not None:
        assert response.json()["detail"]["field"] == field


@pytest.mark.parametrize("price", ["0", "-1"])
def test_manual_single_price_must_be_positive(client: TestClient, price: str) -> None:
    response = client.post(
        "/api/prices/manual",
        json={
            "asset": "BTC",
            "date": "2026-01-01",
            "price_eur": price,
            "source": "Synthetischer Beleg",
            "reason": "Grenzwertprüfung",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "valuation_invalid_price"


def test_import_transform_manual_valuation_and_superseding(client: TestClient) -> None:
    ledger = (
        "txid,time,type,asset,amount,fee,subtype\n"
        "R1,2026-01-02 03:04:05,earn,XXBT,1.25,0,reward\n"
    )
    imported = client.post(
        "/api/imports/kraken?transform=true",
        files={"file": ("ledger.csv", ledger.encode())},
    )
    assert imported.status_code == 200
    body = imported.json()
    assert body["duplicate"] is False
    assert body["transformation"]["requirements"] == 1
    assert body["transformation"]["checked"] == 1
    duplicate_import = client.post(
        "/api/imports/kraken?transform=true",
        files={"file": ("ledger.csv", ledger.encode())},
    )
    assert duplicate_import.status_code == 200
    duplicate_body = duplicate_import.json()
    assert duplicate_body["duplicate"] is True
    assert (
        duplicate_body["transformation"]["run_id"] == body["transformation"]["run_id"]
    )
    assert client.get("/api/transformations").json()["total"] == 1
    assert client.get("/api/events?event_type=acquisition").json()["total"] == 1
    assert client.get("/api/valuation-requirements").json()["total"] == 1
    import_detail = client.get(f"/api/imports/{body['session_id']}?include_raw=true")
    assert import_detail.status_code == 200
    assert import_detail.json()["records"][0]["payload"]["txid"] == "R1"
    transformation_detail = client.get(
        f"/api/transformations/{body['transformation']['run_id']}"
    )
    assert transformation_detail.status_code == 200
    assert transformation_detail.json()["decisions"][0]["raw_import_record_id"]
    requirement = client.get("/api/valuation-requirements").json()["items"][0]
    assert requirement["event_type"] == "AcquisitionLot"
    assert client.get("/api/valuation-requirements?asset=ETH").json()["total"] == 0
    event = client.get("/api/events?event_type=acquisition").json()["items"][0]
    event_detail = client.get(f"/api/events/acquisition/{event['id']}?include_raw=true")
    assert event_detail.status_code == 200
    assert event_detail.json()["provenance"][0]["raw_record"]["payload"]["txid"] == "R1"
    manual = client.post(
        "/api/prices/manual",
        json={
            "asset": "BTC",
            "date": "2026-01-02",
            "price_eur": "40000.123456789012345678",
            "source": "Synthetischer Tagesbeleg",
            "reason": "Provider im Test deaktiviert",
        },
    )
    assert manual.status_code == 200
    first = client.post("/api/valuations")
    assert first.status_code == 200
    assert first.json()["resolved"] == 1
    assert client.get("/api/valuation-requirements?status=pending").json()["total"] == 0
    decisions = client.get("/api/valuations").json()["items"]
    assert len(decisions) == 1
    assert decisions[0]["method"] == "manual_daily_price"
    assert decisions[0]["eur_value"] == "50000.15432098626543209750"
    duplicate = client.post("/api/valuations")
    assert duplicate.status_code == 200
    assert duplicate.json()["checked"] == 0
    replacement = client.post("/api/valuations?method_version=eur-valuation-v2")
    assert replacement.status_code == 200
    assert replacement.json()["resolved"] == 1
    decisions = client.get("/api/valuations").json()["items"]
    assert len(decisions) == 2
    old = next(item for item in decisions if item["version"] == 1)
    assert old["effective_status"] == "superseded"
    new = next(item for item in decisions if item["version"] == 2)
    detail = client.get(f"/api/valuations/{new['id']}")
    assert detail.status_code == 200
    provenance = detail.json()
    assert provenance["supersedes_id"] == old["id"]
    assert provenance["requirement"]["id"] == requirement["id"]
    assert provenance["domain_object"]["external_id"] == "kraken:ledger:R1"
    assert provenance["import_sessions"][0]["id"] == body["session_id"]
    assert provenance["raw_records"][0]["external_id"] == "kraken:ledger:R1"
    assert provenance["daily_price"]["id"] == manual.json()["id"]
    assert provenance["provider_evidence_id"] is None
    assert provenance["provider_evidence"] is None
    assert provenance["audit"]
    dashboard = client.get("/api/dashboard").json()
    assert dashboard["imports"] == 2
    assert dashboard["rewards"] == 1
    assert dashboard["open_valuations"] == 0
    assert dashboard["resolved_valuations"] == 1
    assert client.get("/api/events?event_type=acquisition").json()["total"] == 1
    assert client.get("/api/events?event_type=trade").json()["total"] == 0
    assert client.get("/api/valuations?date_from=2026-02-01").json()["total"] == 0
    assert (
        client.get(
            "/api/valuations?date_from=2026-02-01&date_to=2026-01-01"
        ).status_code
        == 422
    )


def test_import_without_transform_stays_a_pure_import(client: TestClient) -> None:
    ledger = (
        "txid,time,type,asset,amount,fee,subtype\n"
        "PURE-1,2026-01-02 03:04:05,earn,XXBT,1,0,reward\n"
    )
    imported = client.post(
        "/api/imports/kraken",
        files={"file": ("ledger.csv", ledger.encode())},
    )
    assert imported.status_code == 200
    assert imported.json()["transformation"] is None
    assert client.get("/api/transformations").json()["total"] == 0
    assert client.get("/api/valuation-requirements").json()["total"] == 0

    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/imports/kraken"]["post"]
    transform_parameter = next(
        item for item in operation["parameters"] if item["name"] == "transform"
    )
    assert transform_parameter["schema"]["default"] is False
    response_schema = schema["components"]["schemas"]["ImportResultResponse"]
    assert "transformation" in response_schema["properties"]

    transformed = client.post(
        "/api/transformations", json=[imported.json()["session_id"]]
    )
    assert transformed.status_code == 200
    assert transformed.json()["checked"] == 1
    assert transformed.json()["valuation_requirements"] == 1


def test_reusable_transformation_requires_success_and_matching_version() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    import_session_id = uuid4()
    completed_v1 = TransformationRun(
        contract_version="contract-v1",
        status=TransformationStatus.COMPLETED,
        started_at=NOW,
        actor_id="test-suite",
    )
    failed_v1 = TransformationRun(
        contract_version="contract-v1",
        status=TransformationStatus.FAILED,
        started_at=NOW + timedelta(minutes=1),
        actor_id="test-suite",
    )
    completed_v2 = TransformationRun(
        contract_version="contract-v2",
        status=TransformationStatus.COMPLETED,
        started_at=NOW + timedelta(minutes=2),
        actor_id="test-suite",
    )
    with sessions() as database:
        database.add_all((completed_v1, failed_v1, completed_v2))
        database.add_all(
            TransformationRunSession(
                transformation_run_id=run.id,
                import_session_id=import_session_id,
            )
            for run in (completed_v1, failed_v1, completed_v2)
        )
        database.commit()

        assert (
            reusable_transformation_run(database, import_session_id, "contract-v1")
            == completed_v1
        )
        assert (
            reusable_transformation_run(database, import_session_id, "contract-v2")
            == completed_v2
        )
        assert (
            reusable_transformation_run(database, import_session_id, "contract-v3")
            is None
        )


def test_missing_requirement_domain_object_becomes_review() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as database:
        database.add(
            ValuationRequirement(
                asset_code="BTC",
                target_currency="EUR",
                valuation_at=NOW,
                method=ValuationMethod.DAILY_AVERAGE,
                status=ValuationStatus.VALUATION_REQUIRED,
                reason_code="synthetic_missing_domain_object",
                domain_object_type="AcquisitionLot",
                domain_object_id=uuid4(),
                transformation_run_id=uuid4(),
            )
        )
        database.commit()
        result = run_valuations(database, method_version=METHOD_VERSION)
    assert result["checked"] == 1
    assert result["resolved"] == 0
    assert result["reviews"] == 1
    assert result["status"] == "completed_with_review"


def test_existing_provider_evidence_is_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)

    def dependency() -> object:
        with sessions() as session:
            yield session

    day_start = datetime(2026, 1, 8, tzinfo=UTC)
    hourly = tuple(
        PriceObservation(
            observed_at=day_start + timedelta(hours=index),
            price_eur=Decimal("100") + Decimal(index),
        )
        for index in range(24)
    )
    start, end = utc_day_bounds(day_start.date())
    existing_evidence = ProviderEvidence(
        provider="coingecko",
        provider_contract_version="market-chart-range-v1",
        provider_asset_id="ethereum",
        target_currency="EUR",
        requested_from=start,
        requested_to=end,
        fetched_at=NOW,
        http_status=200,
        response_hash=evidence_hash(hourly),
        observation_count=len(hourly),
        observations=[
            {
                "observed_at": item.observed_at.isoformat(),
                "price_eur": str(item.price_eur),
            }
            for item in hourly
        ],
        earliest_observed_at=hourly[0].observed_at,
        latest_observed_at=hourly[-1].observed_at,
    )
    with sessions() as database:
        database.add(existing_evidence)
        database.commit()

    app.dependency_overrides[get_session] = dependency
    settings = get_settings()
    previous_mode = settings.coingecko_api_mode
    settings.coingecko_api_mode = "keyless"
    monkeypatch.setattr(
        CoinGeckoProvider, "observations", lambda *_args, **_kwargs: hourly
    )
    try:
        with TestClient(app) as local_client:
            reward_csv = (
                "txid,time,type,asset,amount,fee,subtype\n"
                "EVIDENCE,2026-01-08 03:04:05,earn,XETH,1,0,reward\n"
            )
            imported = local_client.post(
                "/api/imports/kraken?transform=true",
                files={"file": ("reward.csv", reward_csv.encode())},
            )
            assert imported.status_code == 200
            run = local_client.post("/api/valuations")
            assert run.status_code == 200
            assert run.json()["resolved"] == 1
            price = local_client.get("/api/prices").json()["items"][0]
            detail = local_client.get(f"/api/prices/{price['id']}").json()
            assert detail["provider_evidence"]["id"] == str(existing_evidence.id)
    finally:
        settings.coingecko_api_mode = previous_mode
        app.dependency_overrides.clear()


def test_native_eur_and_automatic_provider_runs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    trade_csv = (
        "txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol\n"
        "T1,O1,XXBTZEUR,2026-01-02 03:04:05,buy,limit,40000,20000,2,0.5\n"
    )
    imported = client.post(
        "/api/imports/kraken?transform=true",
        files={"file": ("trades.csv", trade_csv.encode())},
    )
    assert imported.status_code == 200

    def unexpected_provider_call(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("Native EUR must not call the price provider.")

    monkeypatch.setattr(CoinGeckoProvider, "observations", unexpected_provider_call)
    native = client.post("/api/valuations")
    assert native.status_code == 200
    assert native.json()["resolved"] == 2
    requirements = client.get("/api/valuation-requirements").json()["items"]
    assert len(requirements) == 2
    assert {item["method"] for item in requirements} == {"direct_eur"}
    assert {item["status"] for item in requirements} == {"resolved"}
    native_decisions = client.get("/api/valuations").json()["items"]
    assert {item["method"] for item in native_decisions} == {"native_eur"}
    native_details = [
        client.get(f"/api/valuations/{item['id']}").json() for item in native_decisions
    ]
    native_detail = next(
        item
        for item in native_details
        if item["domain_object"]["type"] == "AcquisitionLot"
    )
    assert native_detail["quantity"] == "0.5"
    assert native_detail["unit_price_eur"] == "40000"
    assert native_detail["eur_value"] == "20000"
    assert native_detail["requirement"] is not None
    assert native_detail["import_sessions"][0]["id"] == imported.json()["session_id"]
    assert native_detail["raw_records"][0]["external_id"] == "kraken:trade:T1"
    assert (
        native_detail["transformation_runs"][0]["id"]
        == imported.json()["transformation"]["run_id"]
    )
    assert native_detail["daily_price"] is None
    assert native_detail["provider_evidence_id"] is None
    assert native_detail["provider_evidence"] is None
    repeated_native = client.post("/api/valuations")
    assert repeated_native.status_code == 200
    assert repeated_native.json()["checked"] == 0
    assert client.get("/api/valuations").json()["total"] == 2

    reward_csv = (
        "txid,time,type,asset,amount,fee,subtype\n"
        "R2,2026-01-03 03:04:05,earn,XETH,2,0,reward\n"
    )
    reward = client.post(
        "/api/imports/kraken?transform=true",
        files={"file": ("reward.csv", reward_csv.encode())},
    )
    assert reward.status_code == 200
    day_start = datetime(2026, 1, 3, tzinfo=UTC)
    hourly = tuple(
        PriceObservation(
            observed_at=day_start + timedelta(hours=index),
            price_eur=Decimal("2000") + Decimal(index),
        )
        for index in range(24)
    )
    monkeypatch.setattr(
        CoinGeckoProvider,
        "observations",
        lambda *_args, **_kwargs: hourly,
    )
    settings = get_settings()
    previous_mode = settings.coingecko_api_mode
    settings.coingecko_api_mode = "keyless"
    try:
        automatic = client.post("/api/valuations")
    finally:
        settings.coingecko_api_mode = previous_mode
    assert automatic.status_code == 200
    assert automatic.json()["resolved"] == 1
    prices = client.get("/api/prices?asset=ETH&method=daily_average_hourly").json()
    assert prices["total"] == 1
    detail = client.get(f"/api/prices/{prices['items'][0]['id']}").json()
    assert detail["provider_evidence"]["provider"] == "coingecko"
    assert detail["provider_evidence"]["observation_count"] == 24
    automatic_decision = next(
        item
        for item in client.get("/api/valuations").json()["items"]
        if item["asset"] == "ETH"
    )
    automatic_detail = client.get(f"/api/valuations/{automatic_decision['id']}").json()
    assert automatic_detail["daily_price"] is not None
    assert automatic_detail["provider_evidence_id"] is not None
    assert automatic_detail["provider_evidence"]["provider"] == "coingecko"

    same_day_reward = (
        "txid,time,type,asset,amount,fee,subtype\n"
        "R2B,2026-01-03 05:04:05,earn,XETH,1,0,reward\n"
    )
    assert (
        client.post(
            "/api/imports/kraken?transform=true",
            files={"file": ("reward.csv", same_day_reward.encode())},
        ).status_code
        == 200
    )
    identical = client.post("/api/valuations?refresh_prices=true")
    assert identical.status_code == 200
    assert identical.json()["resolved"] == 1
    assert client.get("/api/prices?asset=ETH").json()["total"] == 1

    cached_reward = same_day_reward.replace("R2B", "R2C")
    assert (
        client.post(
            "/api/imports/kraken?transform=true",
            files={"file": ("reward.csv", cached_reward.encode())},
        ).status_code
        == 200
    )
    monkeypatch.setattr(CoinGeckoProvider, "observations", unexpected_provider_call)
    cached = client.post("/api/valuations")
    assert cached.status_code == 200
    assert cached.json()["resolved"] == 1

    changed_reward = same_day_reward.replace("R2B", "R2D")
    assert (
        client.post(
            "/api/imports/kraken?transform=true",
            files={"file": ("reward.csv", changed_reward.encode())},
        ).status_code
        == 200
    )
    changed_hourly = tuple(
        PriceObservation(
            observed_at=item.observed_at,
            price_eur=item.price_eur + Decimal("1"),
        )
        for item in hourly
    )
    monkeypatch.setattr(
        CoinGeckoProvider,
        "observations",
        lambda *_args, **_kwargs: changed_hourly,
    )
    refreshed = client.post("/api/valuations?refresh_prices=true")
    assert refreshed.status_code == 200
    assert refreshed.json()["resolved"] == 1
    assert client.get("/api/prices?asset=ETH").json()["total"] == 2


def test_disabled_provider_creates_visible_review(client: TestClient) -> None:
    reward_csv = (
        "txid,time,type,asset,amount,fee,subtype\n"
        "R3,2026-01-04 03:04:05,earn,XXBT,1,0,reward\n"
    )
    assert (
        client.post(
            "/api/imports/kraken?transform=true",
            files={"file": ("reward.csv", reward_csv.encode())},
        ).status_code
        == 200
    )
    run = client.post("/api/valuations")
    assert run.status_code == 200
    assert run.json()["status"] == "completed_with_review"
    reviews = client.get("/api/reviews").json()
    assert reviews["total"] == 1
    assert client.get("/api/dashboard").json()["review_cases"] == 1
    assert reviews["items"][0]["code"] == "valuation_provider_disabled"
    detail = client.get(f"/api/reviews/{reviews['items'][0]['id']}")
    assert detail.status_code == 200
    assert detail.json()["audit"][0]["metadata"]["temporary"] is False


def test_transformation_review_detail_is_explainable(client: TestClient) -> None:
    trade_csv = (
        "txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol\n"
        "BADPAIR,O1,UNKNOWN,2026-01-02 03:04:05,buy,limit,1,1,0,1\n"
    )
    imported = client.post(
        "/api/imports/kraken?transform=true",
        files={"file": ("trades.csv", trade_csv.encode())},
    )
    assert imported.status_code == 200
    assert imported.json()["transformation"]["status"] == "completed_with_review"
    review = client.get("/api/reviews").json()["items"][0]
    detail = client.get(f"/api/reviews/{review['id']}")
    assert detail.status_code == 200
    assert detail.json()["kind"] == "transformation"
    assert detail.json()["code"] == "trade_pair_unresolved"


def test_valuation_repository_failure_rolls_back(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    reward_csv = (
        "txid,time,type,asset,amount,fee,subtype\n"
        "R4,2026-01-05 03:04:05,earn,XXBT,1,0,reward\n"
    )
    assert (
        client.post(
            "/api/imports/kraken?transform=true",
            files={"file": ("reward.csv", reward_csv.encode())},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/prices/manual",
            json={
                "asset": "BTC",
                "date": "2026-01-05",
                "price_eur": "1",
                "source": "Synthetischer Nachweis",
                "reason": "Rollback-Test",
            },
        ).status_code
        == 200
    )

    def fail_commit(_: Session) -> None:
        raise RuntimeError("synthetic repository failure")

    monkeypatch.setattr(Session, "commit", fail_commit)
    with TestClient(app, raise_server_exceptions=False) as safe_client:
        failed = safe_client.post("/api/valuations")
    assert failed.status_code == 500
    assert failed.json()["detail"]["code"] == "internal_server_error"
    monkeypatch.undo()
    assert client.get("/api/valuations").json()["total"] == 0


def test_incomplete_provider_day_becomes_review(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    reward_csv = (
        "txid,time,type,asset,amount,fee,subtype\n"
        "R5,2026-01-06 03:04:05,earn,XETH,1,0,reward\n"
    )
    assert (
        client.post(
            "/api/imports/kraken?transform=true",
            files={"file": ("reward.csv", reward_csv.encode())},
        ).status_code
        == 200
    )
    start = datetime(2026, 1, 6, tzinfo=UTC)
    monkeypatch.setattr(
        CoinGeckoProvider,
        "observations",
        lambda *_args, **_kwargs: tuple(
            PriceObservation(
                observed_at=start + timedelta(hours=index),
                price_eur=Decimal("2"),
            )
            for index in range(19)
        ),
    )
    settings = get_settings()
    previous_mode = settings.coingecko_api_mode
    settings.coingecko_api_mode = "keyless"
    try:
        run = client.post("/api/valuations")
    finally:
        settings.coingecko_api_mode = previous_mode
    assert run.status_code == 200
    assert run.json()["status"] == "completed_with_review"
    decision = client.get("/api/valuations").json()["items"][0]
    assert decision["status"] == "review_required"
    reviews = client.get("/api/reviews").json()
    assert reviews["total"] == 1
    detail = client.get(f"/api/reviews/{reviews['items'][0]['id']}")
    assert detail.status_code == 200
    assert detail.json()["kind"] == "valuation"
    assert detail.json()["code"] == "valuation_incomplete_daily_coverage"


def test_empty_provider_data_becomes_review(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    reward_csv = (
        "txid,time,type,asset,amount,fee,subtype\n"
        "EMPTY,2026-01-07 03:04:05,earn,XETH,1,0,reward\n"
    )
    assert (
        client.post(
            "/api/imports/kraken?transform=true",
            files={"file": ("reward.csv", reward_csv.encode())},
        ).status_code
        == 200
    )
    monkeypatch.setattr(CoinGeckoProvider, "observations", lambda *_args, **_kwargs: ())
    settings = get_settings()
    previous_mode = settings.coingecko_api_mode
    settings.coingecko_api_mode = "keyless"
    try:
        run = client.post("/api/valuations")
    finally:
        settings.coingecko_api_mode = previous_mode
    assert run.status_code == 200
    assert run.json()["status"] == "completed_with_review"
    review = client.get("/api/reviews").json()["items"][0]
    assert review["code"] == "valuation_no_price_data"
