import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
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
    LEGACY_METHOD_VERSION,
    METHOD_VERSION,
    DailyPrice,
    FeeTaxClassification,
    FeeTaxReviewStatus,
    PriceMethod,
    PriceObservation,
    PriceProviderError,
    ProviderEvidence,
    RewardValuationError,
    ValuationDecision,
    ValuationDecisionStatus,
    ValuationRun,
    ValuationRunStatus,
    calculate_eur_value,
    calculate_reward_valuation,
    daily_average,
    display_cents,
    evidence_hash,
    exact_decimal_multiply,
    exact_decimal_sum,
    transition_valuation_run,
    utc_day_bounds,
)
from app.database.base import Base
from app.database.session import get_session
from app.infrastructure import coingecko
from app.infrastructure.coingecko import (
    ASSET_IDS,
    MAPPING_VERSION,
    CoinGeckoProvider,
)
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


@pytest.mark.parametrize(
    ("gross", "fee", "net", "unit"),
    [
        ("1", "0.1", "0.9", "100"),
        (
            "0.000000000000000003",
            "0.000000000000000001",
            "0.000000000000000002",
            "1.234567890123456789",
        ),
        ("2.34567891", "0", "2.34567891", "40000.123456789012345678"),
        (
            "12345678901234567890.123456789",
            "0.123456789",
            "12345678901234567890",
            "987654321.123456789012345678",
        ),
    ],
)
def test_reward_valuation_v2_preserves_exact_components(
    gross: str, fee: str, net: str, unit: str
) -> None:
    result = calculate_reward_valuation(
        net_quantity=Decimal(net),
        gross_quantity=Decimal(gross),
        fee_quantity=Decimal(fee),
        asset_code="ETH",
        fee_asset="ETH" if Decimal(fee) else None,
        unit_price_eur=Decimal(unit),
        method_version=METHOD_VERSION,
    )
    assert result.gross_income_eur == exact_decimal_multiply(
        Decimal(gross), Decimal(unit)
    )
    assert result.fee_value_eur == exact_decimal_multiply(Decimal(fee), Decimal(unit))
    assert result.net_acquisition_value_eur == exact_decimal_multiply(
        Decimal(net), Decimal(unit)
    )
    assert result.gross_income_eur == exact_decimal_sum(
        (result.net_acquisition_value_eur, result.fee_value_eur)
    )
    assert result.fee_tax_review_status is (
        FeeTaxReviewStatus.REVIEW_REQUIRED
        if Decimal(fee)
        else FeeTaxReviewStatus.NOT_REQUIRED
    )
    assert result.fee_tax_classification is (
        FeeTaxClassification.WERBUNGSKOSTEN_CANDIDATE
        if Decimal(fee)
        else FeeTaxClassification.NOT_APPLICABLE
    )


def test_reward_valuation_v2_uses_operand_driven_decimal_precision() -> None:
    unit = Decimal("1.393395260395307108333333333")
    gross = Decimal("0.0259233001")
    net = Decimal("0.0194382301")
    fee = Decimal("0.00648507")

    with localcontext() as legacy_context:
        legacy_context.prec = 28
        rounded_gross = gross * unit
        rounded_parts = net * unit + fee * unit
    assert rounded_gross != rounded_parts

    result = calculate_reward_valuation(
        net_quantity=net,
        gross_quantity=gross,
        fee_quantity=fee,
        asset_code="KAVA",
        fee_asset="KAVA",
        unit_price_eur=unit,
        method_version=METHOD_VERSION,
    )

    assert result.gross_income_eur == Decimal("0.0361214034931451908009882108246922333")
    assert result.net_acquisition_value_eur == Decimal(
        "0.0270851376918133965319489608268539233"
    )
    assert result.fee_value_eur == Decimal("0.00903626580133179426903924999783831")
    assert isinstance(result.gross_income_eur, Decimal)
    assert isinstance(result.fee_value_eur, Decimal)
    assert isinstance(result.net_acquisition_value_eur, Decimal)
    assert result.gross_income_eur == exact_decimal_sum(
        (result.net_acquisition_value_eur, result.fee_value_eur)
    )
    assert result.gross_income_eur.as_tuple().exponent == -37
    decision = ValuationDecision(
        valuation_requirement_id=uuid4(),
        valuation_run_id=uuid4(),
        domain_object_type="AcquisitionLot",
        domain_object_id=uuid4(),
        asset_code="KAVA",
        quantity=net,
        valuation_at=NOW,
        price_date=DAY,
        method=PriceMethod.DAILY_AVERAGE_HOURLY,
        unit_price_eur=unit,
        eur_value=result.net_acquisition_value_eur,
        price_source="Synthetischer Präzisionstest",
        provider="synthetic",
        provider_object_id=None,
        provider_contract_version="synthetic-v1",
        method_version=METHOD_VERSION,
        sample_count=24,
        fetched_at=NOW,
        decided_at=NOW,
        status=ValuationDecisionStatus.RESOLVED,
        reason_code="valuation_resolved",
        gross_quantity=gross,
        fee_quantity=fee,
        net_quantity=net,
        gross_income_eur=result.gross_income_eur,
        fee_value_eur=result.fee_value_eur,
        net_acquisition_value_eur=result.net_acquisition_value_eur,
        valuation_basis="staking_reward_components_v2",
        fee_tax_classification=FeeTaxClassification.WERBUNGSKOSTEN_CANDIDATE,
        fee_tax_review_status=FeeTaxReviewStatus.REVIEW_REQUIRED,
    )
    assert decision.net_acquisition_value_eur is not None
    assert decision.fee_value_eur is not None
    assert decision.gross_income_eur == exact_decimal_sum(
        (decision.net_acquisition_value_eur, decision.fee_value_eur)
    )


def test_exact_decimal_arithmetic_supports_different_exponents() -> None:
    values = (Decimal("9.999E+40"), Decimal("0.000000000000000000000000000123"))
    total = exact_decimal_sum(values)

    assert total == Decimal(
        "99990000000000000000000000000000000000000.000000000000000000000000000123"
    )
    assert exact_decimal_multiply(
        Decimal("0.00000001"), Decimal("123456789.123456789")
    ) == Decimal("1.23456789123456789")
    with pytest.raises(ValueError, match="finite"):
        exact_decimal_sum((Decimal("NaN"),))


def test_reward_valuation_versions_and_legacy_lot_fallback() -> None:
    legacy = calculate_reward_valuation(
        net_quantity=Decimal("0.9"),
        gross_quantity=Decimal("99"),
        fee_quantity=Decimal("98.1"),
        asset_code="BTC",
        fee_asset="OTHER",
        unit_price_eur=Decimal("100"),
        method_version=LEGACY_METHOD_VERSION,
    )
    assert legacy.gross_income_eur is legacy.fee_value_eur is None
    assert legacy.net_acquisition_value_eur == Decimal("90")
    assert legacy.valuation_basis == "staking_reward_net_quantity_legacy_v1"
    fallback = calculate_reward_valuation(
        net_quantity=Decimal("0.9"),
        gross_quantity=None,
        fee_quantity=None,
        asset_code="BTC",
        fee_asset=None,
        unit_price_eur=Decimal("100"),
        method_version=METHOD_VERSION,
    )
    assert fallback.gross_quantity == fallback.net_quantity == Decimal("0.9")
    assert fallback.fee_quantity == fallback.fee_value_eur == Decimal("0")
    assert fallback.valuation_basis == "staking_reward_legacy_lot_fallback_v2"


@pytest.mark.parametrize(
    ("gross", "fee", "net", "fee_asset", "code"),
    [
        ("1", "0.2", "0.9", "BTC", "valuation_reward_quantity_inconsistent"),
        ("0", "0", "0.9", "BTC", "valuation_reward_quantity_inconsistent"),
        ("1", "-0.1", "1.1", "BTC", "valuation_reward_quantity_inconsistent"),
        ("1", "1.1", "0.1", "BTC", "valuation_reward_quantity_inconsistent"),
        ("1", "0.1", "0.9", "ETH", "valuation_reward_fee_asset_mismatch"),
    ],
)
def test_reward_valuation_v2_rejects_inconsistent_components(
    gross: str, fee: str, net: str, fee_asset: str, code: str
) -> None:
    with pytest.raises(RewardValuationError) as raised:
        calculate_reward_valuation(
            net_quantity=Decimal(net),
            gross_quantity=Decimal(gross),
            fee_quantity=Decimal(fee),
            asset_code="BTC",
            fee_asset=fee_asset,
            unit_price_eur=Decimal("100"),
            method_version=METHOD_VERSION,
        )
    assert raised.value.code == code


def test_reward_valuation_v2_requires_fee_when_gross_is_present() -> None:
    with pytest.raises(RewardValuationError) as raised:
        calculate_reward_valuation(
            net_quantity=Decimal("1"),
            gross_quantity=Decimal("1"),
            fee_quantity=None,
            asset_code="BTC",
            fee_asset=None,
            unit_price_eur=Decimal("100"),
            method_version=METHOD_VERSION,
        )
    assert raised.value.code == "valuation_reward_quantity_inconsistent"
    with pytest.raises(RewardValuationError) as fee_without_gross:
        calculate_reward_valuation(
            net_quantity=Decimal("0.9"),
            gross_quantity=None,
            fee_quantity=Decimal("0.1"),
            asset_code="BTC",
            fee_asset="BTC",
            unit_price_eur=Decimal("100"),
            method_version=METHOD_VERSION,
        )
    assert fee_without_gross.value.code == "valuation_reward_quantity_inconsistent"


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
        gross_quantity=Decimal("0.1"),
        fee_quantity=Decimal("0"),
        net_quantity=Decimal("0.1"),
        gross_income_eur=Decimal("0.1"),
        fee_value_eur=Decimal("0"),
        net_acquisition_value_eur=Decimal("0.1"),
        valuation_basis="synthetic_components",
        fee_tax_classification=FeeTaxClassification.NOT_APPLICABLE,
        fee_tax_review_status=FeeTaxReviewStatus.NOT_REQUIRED,
    )
    assert decision.eur_value == Decimal("0.1")
    with pytest.raises(ValueError):
        replace(decision, id=uuid4(), quantity=Decimal("0"))
    invalid_changes = (
        {"net_quantity": Decimal("0")},
        {"net_quantity": Decimal("0.2")},
        {"gross_quantity": Decimal("0")},
        {"fee_quantity": Decimal("-0.1")},
        {"gross_income_eur": Decimal("0")},
        {"fee_value_eur": Decimal("-0.1")},
        {"net_acquisition_value_eur": Decimal("0")},
        {"net_acquisition_value_eur": Decimal("0.2")},
        {"gross_quantity": Decimal("0.2")},
        {"gross_income_eur": Decimal("0.2")},
        {"fee_value_eur": None},
    )
    for changes in invalid_changes:
        with pytest.raises(ValueError):
            replace(decision, id=uuid4(), **changes)


class Response:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


@pytest.fixture(autouse=True)
def no_coingecko_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(coingecko.time, "sleep", lambda _: None)


def provider(mode: str = "keyless", key: str | None = None) -> CoinGeckoProvider:
    return CoinGeckoProvider(
        base_url="https://prices.invalid",
        mode=mode,
        api_key=key,
        timeout_seconds=1,
        retries=1,
    )


def test_coingecko_asset_mapping_v2_is_an_explicit_allowlist() -> None:
    expected = {
        "ADA": "cardano",
        "ATOM": "cosmos",
        "BTC": "bitcoin",
        "DOT": "polkadot",
        "EIGEN": "eigenlayer",
        "ETH": "ethereum",
        "GRT": "the-graph",
        "KAVA": "kava",
        "XTZ": "tezos",
    }

    assert expected == ASSET_IDS
    assert MAPPING_VERSION == "coingecko-asset-map-v2"
    for asset, provider_id in expected.items():
        assert provider().asset_id(asset) == provider_id
        assert provider().asset_id(asset.lower()) == provider_id

    assert provider().asset_id("EIGEN") == "eigenlayer"
    assert provider().asset_id("ATOM") == "cosmos"
    with pytest.raises(PriceProviderError) as missing:
        provider().asset_id("UNLISTED")
    assert missing.value.code == "valuation_asset_mapping_missing"
    assert MAPPING_VERSION in str(missing.value)

    source = Path(coingecko.__file__).read_text(encoding="utf-8")
    assert "/coins/list" not in source


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
    assert MAPPING_VERSION in str(missing.value)
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
    assert CoinGeckoProvider._retry_delay(0, "31") == 31
    assert CoinGeckoProvider._retry_delay(0, "not-a-date") == 1
    future_naive = (datetime.now(UTC) + timedelta(minutes=1)).strftime(
        "%a, %d %b %Y %H:%M:%S"
    )
    assert 0 < CoinGeckoProvider._retry_delay(0, future_naive) <= 60

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
    system = client.get("/api/system/status").json()
    assert system["api_key_configured"] is False
    assert system["asset_mapping_version"] == MAPPING_VERSION
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
    assert body["transformation"]["contract_version"] == "kraken-domain-v2"
    assert body["transformation"]["created_objects"] == 2
    assert body["transformation"]["reused_objects"] == 0
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
    assert duplicate_body["transformation"]["contract_version"] == "kraken-domain-v2"
    assert duplicate_body["transformation"]["created_objects"] == 2
    assert duplicate_body["transformation"]["reused_objects"] == 0
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
    first = client.post("/api/valuations?method_version=eur-valuation-v1")
    assert first.status_code == 200
    assert first.json()["resolved"] == 1
    assert first.json()["method_version"] == "eur-valuation-v1"
    assert first.json()["gross_income_total_eur"] == "0"
    assert first.json()["fee_candidate_total_eur"] == "0"
    assert first.json()["net_acquisition_total_eur"] == "50000.15432098626543209750"
    assert client.get("/api/valuation-requirements?status=pending").json()["total"] == 0
    decisions = client.get("/api/valuations").json()["items"]
    assert len(decisions) == 1
    assert decisions[0]["method"] == "manual_daily_price"
    assert decisions[0]["eur_value"] == "50000.15432098626543209750"
    assert decisions[0]["method_version"] == LEGACY_METHOD_VERSION
    assert decisions[0]["gross_quantity"] is None
    assert decisions[0]["gross_income_eur"] is None
    assert decisions[0]["fee_value_eur"] is None
    assert decisions[0]["net_quantity"] == "1.25"
    assert decisions[0]["net_acquisition_value_eur"] == decisions[0]["eur_value"]
    duplicate = client.post("/api/valuations?method_version=eur-valuation-v1")
    assert duplicate.status_code == 200
    assert duplicate.json()["checked"] == 0
    replacement = client.post("/api/valuations")
    assert replacement.status_code == 200
    assert replacement.json()["resolved"] == 1
    assert replacement.json()["method_version"] == METHOD_VERSION
    assert replacement.json()["gross_income_total_eur"] == (
        "50000.15432098626543209750"
    )
    assert replacement.json()["fee_candidate_total_eur"] == "0"
    assert replacement.json()["net_acquisition_total_eur"] == (
        "50000.15432098626543209750"
    )
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
    assert provenance["gross_quantity"] == "1.25"
    assert provenance["fee_quantity"] == "0"
    assert provenance["net_quantity"] == "1.25"
    assert provenance["gross_income_eur"] == "50000.15432098626543209750"
    assert provenance["fee_value_eur"] == "0E-18"
    assert provenance["net_acquisition_value_eur"] == ("50000.15432098626543209750")
    assert provenance["valuation_basis"] == "staking_reward_components_v2"
    assert provenance["fee_tax_classification"] == "not_applicable"
    assert provenance["fee_tax_review_status"] == "not_required"
    assert provenance["method_version"] == METHOD_VERSION
    assert provenance["rounding_rule"] == "ROUND_HALF_UP_DISPLAY_ONLY"
    assert provenance["audit"]
    dashboard = client.get("/api/dashboard").json()
    assert dashboard["imports"] == 2
    assert dashboard["rewards"] == 1
    assert dashboard["open_valuations"] == 0
    assert dashboard["resolved_valuations"] == 1
    assert client.get("/api/events?event_type=acquisition").json()["total"] == 1
    assert client.get("/api/events?event_type=trade").json()["total"] == 0


def test_staking_reward_api_reports_gross_fee_and_net_values(
    client: TestClient,
) -> None:
    reward = (
        "txid,time,type,asset,amount,fee,subtype\n"
        "FEE-REWARD,2026-01-02 03:04:05,earn,XXBT,1,0.1,reward\n"
    )
    imported = client.post(
        "/api/imports/kraken?transform=true",
        files={"file": ("reward.csv", reward.encode())},
    )
    assert imported.status_code == 200
    assert (
        client.post(
            "/api/prices/manual",
            json={
                "asset": "BTC",
                "date": "2026-01-02",
                "price_eur": "100",
                "source": "Synthetischer Tagesbeleg",
                "reason": "Komponentenvertrag",
            },
        ).status_code
        == 200
    )

    response = client.post("/api/valuations")

    assert response.status_code == 200
    assert response.json() == {
        "id": response.json()["id"],
        "status": "completed",
        "method_version": "eur-valuation-v2",
        "checked": 1,
        "resolved": 1,
        "reviews": 0,
        "gross_income_total_eur": "100",
        "fee_candidate_total_eur": "10.0",
        "net_acquisition_total_eur": "90.0",
    }
    item = client.get("/api/valuations").json()["items"][0]
    assert item["quantity"] == item["net_quantity"] == "0.9"
    assert item["gross_quantity"] == "1"
    assert item["fee_quantity"] == "0.1"
    assert item["gross_income_eur"] == "100"
    assert item["fee_value_eur"] == "10.0"
    assert item["net_acquisition_value_eur"] == item["eur_value"] == "90.0"
    assert item["fee_tax_classification"] == "werbungskosten_candidate"
    assert item["fee_tax_review_status"] == "review_required"
    assert client.get("/api/inventory-lots").json()["total"] == 0
    assert client.get("/api/tax-calculations").json()["total"] == 0
    repeated = client.post("/api/valuations")
    assert repeated.json()["checked"] == repeated.json()["resolved"] == 0

    calculated = client.post("/api/tax-calculations", json={"year": 2026})
    assert calculated.status_code == 200
    assert calculated.json()["status"] == "completed_with_review"
    inventory = client.get("/api/inventory-lots?year=2026").json()["items"]
    assert inventory[0]["original_quantity"] == "0.9"
    assert inventory[0]["acquisition_value_eur"] == "90.0"
    journal = client.get("/api/tax-journal?year=2026").json()["items"]
    earn = next(entry for entry in journal if entry["type"] == "earn_inflow")
    assert earn["quantity"] == "0.9"
    assert earn["eur_value"] == "100"
    reviews = client.get("/api/reviews").json()["items"]
    assert any(
        item["code"] == "tax_staking_platform_fee_candidate_review" for item in reviews
    )
    summary = client.get("/api/tax-summary?year=2026").json()
    assert summary["gross_staking_income"] == "100"
    assert summary["staking_fee_candidates"] == "10.0"
    assert summary["provisional_net_staking_income"] == "90.0"


def test_historical_v1_reward_is_not_used_as_unverified_gross_tax_income(
    client: TestClient,
) -> None:
    reward = (
        "txid,time,type,asset,amount,fee,subtype\n"
        "LEGACY-VALUE,2026-01-02 03:04:05,earn,XXBT,1,0,reward\n"
    )
    assert (
        client.post(
            "/api/imports/kraken?transform=true",
            files={"file": ("legacy.csv", reward.encode())},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/prices/manual",
            json={
                "asset": "BTC",
                "date": "2026-01-02",
                "price_eur": "100",
                "source": "Historischer Testbeleg",
                "reason": "v1-Reproduzierbarkeit",
            },
        ).status_code
        == 200
    )
    valued = client.post("/api/valuations?method_version=eur-valuation-v1")
    assert valued.status_code == 200
    assert valued.json()["net_acquisition_total_eur"] == "100"
    calculated = client.post("/api/tax-calculations", json={"year": 2026})
    assert calculated.status_code == 200
    assert calculated.json()["status"] == "completed_with_review"
    journal = client.get("/api/tax-journal?year=2026").json()["items"]
    assert journal[0]["type"] == "review"
    assert journal[0]["eur_value"] == "0"
    reviews = client.get("/api/reviews").json()["items"]
    assert any(item["code"] == "tax_reward_gross_income_missing" for item in reviews)
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


@pytest.mark.parametrize(
    ("gross", "fee_asset", "reason_code"),
    [
        ("1.1", "BTC", "valuation_reward_quantity_inconsistent"),
        ("1", "ETH", "valuation_reward_fee_asset_mismatch"),
    ],
)
def test_reward_component_conflict_becomes_review_before_price_lookup(
    gross: str, fee_asset: str, reason_code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.entities import AuditEvent
    from app.core.tax import InventoryLot, TaxCalculationRun
    from app.core.transformation import (
        AcquisitionLot,
        AcquisitionType,
        TaxTreatmentHint,
    )

    provider_calls: list[str] = []

    def observations_not_expected(
        _provider: CoinGeckoProvider,
        asset: str,
        _target_currency: str,
        _start: datetime,
        _end: datetime,
    ) -> tuple[PriceObservation, ...]:
        provider_calls.append(asset)
        return ()

    monkeypatch.setattr(CoinGeckoProvider, "observations", observations_not_expected)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    lot = AcquisitionLot(
        stable_key="reward-conflict",
        payload_hash="a" * 64,
        asset_raw_code="BTC",
        asset_code="BTC",
        asset_mapping_version="synthetic-v1",
        quantity=Decimal("0.9"),
        gross_quantity=Decimal(gross),
        fee_quantity=Decimal("0.1"),
        fee_asset=fee_asset,
        occurred_at=NOW,
        acquisition_type=AcquisitionType.STAKING_REWARD,
        provider="kraken",
        account_scope="default",
        wallet_scope="kraken-spot",
        external_id="synthetic-conflict",
        transformation_version="kraken-domain-v2",
        valuation_status=ValuationStatus.VALUATION_REQUIRED,
        tax_treatment_hint=TaxTreatmentHint.PASSIVE_STAKING_REWARD,
    )
    requirement = ValuationRequirement(
        asset_code="BTC",
        target_currency="EUR",
        valuation_at=NOW,
        method=ValuationMethod.DAILY_AVERAGE,
        status=ValuationStatus.VALUATION_REQUIRED,
        reason_code="reward_inflow",
        domain_object_type="AcquisitionLot",
        domain_object_id=lot.id,
        transformation_run_id=uuid4(),
    )
    with sessions() as database:
        database.add_all((lot, requirement))
        database.commit()
        result = run_valuations(database, method_version=METHOD_VERSION)
        unchanged_lot = database.get(AcquisitionLot, lot.id)
        assert unchanged_lot is not None
        assert unchanged_lot.quantity == Decimal("0.9")
        assert unchanged_lot.gross_quantity == Decimal(gross)
        assert unchanged_lot.fee_quantity == Decimal("0.1")
        assert unchanged_lot.fee_asset == fee_asset
        assert provider_calls == []
        assert database.scalar(select(func.count()).select_from(DailyPrice)) == 0
        assert database.scalar(select(func.count()).select_from(ProviderEvidence)) == 0
        assert database.scalar(select(func.count()).select_from(ValuationDecision)) == 0
        assert database.scalar(select(func.count()).select_from(InventoryLot)) == 0
        assert database.scalar(select(func.count()).select_from(TaxCalculationRun)) == 0
        review_audits = tuple(
            event
            for event in database.scalars(select(AuditEvent))
            if event.event_type == "valuation.review_created"
        )
        assert review_audits[-1].metadata["reason_code"] == reason_code
        assert review_audits[-1].metadata["method_version"] == METHOD_VERSION
        assert review_audits[-1].metadata["status"] == "review_required"
        assert review_audits[-1].metadata["valuation_basis"] == (
            "staking_reward_components_v2"
        )
        review_id = review_audits[-1].id

        repeated = run_valuations(database, method_version=METHOD_VERSION)
        repeated_reviews = tuple(
            event
            for event in database.scalars(select(AuditEvent))
            if event.event_type == "valuation.review_created"
        )
        assert len(repeated_reviews) == 1
        assert repeated["checked"] == repeated["resolved"] == repeated["reviews"] == 0

    def dependency() -> object:
        with sessions() as database:
            yield database

    app.dependency_overrides[get_session] = dependency
    try:
        with TestClient(app) as review_client:
            listed = review_client.get("/api/reviews").json()["items"]
            assert any(item["code"] == reason_code for item in listed)
            detail = review_client.get(f"/api/reviews/{review_id}")
            assert detail.status_code == 200
            assert detail.json()["code"] == reason_code
            valuations = review_client.get("/api/valuations").json()["items"]
            valuation_review = next(
                item for item in valuations if item["id"] == str(review_id)
            )
            assert valuation_review["status"] == "review_required"
            assert valuation_review["reason_code"] == reason_code
            assert valuation_review["unit_price_eur"] is None
            valuation_detail = review_client.get(f"/api/valuations/{review_id}")
            assert valuation_detail.status_code == 200
            assert valuation_detail.json()["daily_price"] is None
            assert valuation_detail.json()["provider_evidence"] is None
    finally:
        app.dependency_overrides.clear()
    assert result["checked"] == result["reviews"] == 1
    assert result["resolved"] == 0
    assert result["status"] == "completed_with_review"


def test_55_reward_matrix_reuses_one_price_per_asset_and_creates_no_tax_state() -> None:
    from app.core.tax import InventoryLot, TaxCalculationRun
    from app.core.transformation import (
        AcquisitionLot,
        AcquisitionType,
        TaxTreatmentHint,
    )

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    assets = ("ADA", "ATOM", "BTC", "DOT", "EIGEN", "ETH", "GRT", "KAVA", "XTZ")
    price_date = date(2026, 1, 2)
    unit_price = Decimal("1.393395260395307108333333333")
    with sessions() as database:
        transformation = TransformationRun(
            contract_version="kraken-domain-v2",
            status=TransformationStatus.COMPLETED,
            started_at=NOW,
            completed_at=NOW,
            actor_id="test-suite",
            checked_records=55,
            created_objects=110,
        )
        database.add(transformation)
        for asset in assets:
            database.add(
                DailyPrice(
                    asset_code=asset,
                    price_date=price_date,
                    unit_price_eur=unit_price,
                    method=PriceMethod.MANUAL_DAILY_PRICE,
                    source="Synthetische Matrix",
                    provider="manual",
                    provider_contract_version="manual-v1",
                    evidence_hash=(asset.lower() * 64)[:64],
                    sample_count=1,
                    fetched_at=NOW,
                    status=ValuationDecisionStatus.RESOLVED,
                )
            )
        for index in range(55):
            asset = assets[index % len(assets)]
            lot = AcquisitionLot(
                stable_key=f"matrix-{index}",
                payload_hash=f"{index:064x}",
                asset_raw_code=asset,
                asset_code=asset,
                asset_mapping_version="kraken-assets-v2",
                quantity=Decimal("0.9"),
                gross_quantity=Decimal("1"),
                fee_quantity=Decimal("0.1"),
                fee_asset=asset,
                occurred_at=datetime(2026, 1, 2, index % 24, tzinfo=UTC),
                acquisition_type=AcquisitionType.STAKING_REWARD,
                provider="kraken",
                account_scope="default",
                wallet_scope="kraken-spot",
                external_id=f"matrix-{index}",
                transformation_version="kraken-domain-v2",
                valuation_status=ValuationStatus.VALUATION_REQUIRED,
                tax_treatment_hint=TaxTreatmentHint.PASSIVE_STAKING_REWARD,
            )
            database.add(lot)
            database.add(
                ValuationRequirement(
                    asset_code=asset,
                    target_currency="EUR",
                    valuation_at=lot.occurred_at,
                    method=ValuationMethod.DAILY_AVERAGE,
                    status=ValuationStatus.VALUATION_REQUIRED,
                    reason_code="reward_inflow",
                    domain_object_type="AcquisitionLot",
                    domain_object_id=lot.id,
                    transformation_run_id=transformation.id,
                )
            )
        database.commit()

    def dependency() -> object:
        with sessions() as database:
            yield database

    app.dependency_overrides[get_session] = dependency
    try:
        with TestClient(app) as local_client:
            response = local_client.post("/api/valuations")
            assert response.status_code == 200
            result = response.json()
    finally:
        app.dependency_overrides.clear()

    with sessions() as database:
        decisions = tuple(database.scalars(select(ValuationDecision)))
        assert result["checked"] == result["resolved"] == 55
        assert result["reviews"] == 0
        gross_total = Decimal(result["gross_income_total_eur"])
        fee_total = Decimal(result["fee_candidate_total_eur"])
        net_total = Decimal(result["net_acquisition_total_eur"])
        expected_gross = exact_decimal_sum((unit_price,) * 55)
        expected_fee = exact_decimal_sum(
            (exact_decimal_multiply(Decimal("0.1"), unit_price),) * 55
        )
        expected_net = exact_decimal_sum(
            (exact_decimal_multiply(Decimal("0.9"), unit_price),) * 55
        )
        assert gross_total == expected_gross
        assert fee_total == expected_fee
        assert net_total == expected_net
        assert gross_total == exact_decimal_sum((net_total, fee_total))
        assert len(decisions) == 55
        for decision in decisions:
            assert decision.gross_income_eur is not None
            assert decision.net_acquisition_value_eur is not None
            assert decision.fee_value_eur is not None
            assert decision.gross_income_eur == exact_decimal_sum(
                (decision.net_acquisition_value_eur, decision.fee_value_eur)
            )
        assert decisions[0].unit_price_eur == unit_price
        assert len({item.provider_object_id for item in decisions}) == len(assets)
        assert database.scalar(select(func.count()).select_from(DailyPrice)) == 9
        assert database.scalar(select(func.count()).select_from(InventoryLot)) == 0
        assert database.scalar(select(func.count()).select_from(TaxCalculationRun)) == 0


def test_unexpected_reward_decision_error_rolls_back_valuation_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.entities import AuditEvent
    from app.core.transformation import (
        AcquisitionLot,
        AcquisitionType,
        TaxTreatmentHint,
    )

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    lot = AcquisitionLot(
        stable_key="precision-rollback",
        payload_hash="f" * 64,
        asset_raw_code="BTC",
        asset_code="BTC",
        asset_mapping_version="kraken-assets-v2",
        quantity=Decimal("0.0194382301"),
        gross_quantity=Decimal("0.0259233001"),
        fee_quantity=Decimal("0.00648507"),
        fee_asset="BTC",
        occurred_at=datetime(2026, 1, 2, tzinfo=UTC),
        acquisition_type=AcquisitionType.STAKING_REWARD,
        provider="kraken",
        account_scope="default",
        wallet_scope="kraken-spot",
        external_id="precision-rollback",
        transformation_version="kraken-domain-v2",
        valuation_status=ValuationStatus.VALUATION_REQUIRED,
        tax_treatment_hint=TaxTreatmentHint.PASSIVE_STAKING_REWARD,
    )
    requirement = ValuationRequirement(
        asset_code="BTC",
        target_currency="EUR",
        valuation_at=lot.occurred_at,
        method=ValuationMethod.DAILY_AVERAGE,
        status=ValuationStatus.VALUATION_REQUIRED,
        reason_code="reward_inflow",
        domain_object_type="AcquisitionLot",
        domain_object_id=lot.id,
        transformation_run_id=uuid4(),
    )
    with sessions() as database:
        database.add_all((lot, requirement))
        database.commit()

    def dependency() -> object:
        with sessions() as database:
            yield database

    day_start = datetime(2026, 1, 2, tzinfo=UTC)
    hourly = tuple(
        PriceObservation(
            observed_at=day_start + timedelta(hours=index),
            price_eur=Decimal("1.393395260395307108333333333"),
        )
        for index in range(24)
    )

    def fail_decision(_: ValuationDecision) -> None:
        raise RuntimeError("synthetic decision persistence failure")

    settings = get_settings()
    previous_mode = settings.coingecko_api_mode
    settings.coingecko_api_mode = "keyless"
    monkeypatch.setattr(CoinGeckoProvider, "observations", lambda *_: hourly)
    monkeypatch.setattr(ValuationDecision, "__post_init__", fail_decision)
    app.dependency_overrides[get_session] = dependency
    try:
        with TestClient(app, raise_server_exceptions=False) as local_client:
            response = local_client.post("/api/valuations")
            assert response.status_code == 500
            assert response.json()["detail"]["code"] == "internal_server_error"
    finally:
        settings.coingecko_api_mode = previous_mode
        app.dependency_overrides.clear()

    with sessions() as database:
        assert database.scalar(select(func.count()).select_from(ValuationRun)) == 0
        assert database.scalar(select(func.count()).select_from(DailyPrice)) == 0
        assert database.scalar(select(func.count()).select_from(ProviderEvidence)) == 0
        assert database.scalar(select(func.count()).select_from(ValuationDecision)) == 0
        assert database.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_old_provider_evidence_remains_historical(
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
            assert detail["provider_evidence"]["id"] != str(existing_evidence.id)
            with sessions() as database:
                historical = database.get(ProviderEvidence, existing_evidence.id)
                assert historical is not None
                assert historical.provider_contract_version == "market-chart-range-v1"
            assert detail["provider_evidence"]["asset_id"] == "ethereum"
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
