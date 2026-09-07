import base64
import json
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from app.infrastructure import kraken_market
from app.infrastructure.kraken_market import (
    KrakenMarketError,
    KrakenPublicMarketClient,
)
from app.infrastructure.kraken_private import KrakenPrivateClient, KrakenPrivateError

SECRET = base64.b64encode(b"sprint-5b-secret").decode()
NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)


class Response:
    def __init__(self, body: object) -> None:
        self.body = json.dumps(body).encode()

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def private_client(*responses: object) -> KrakenPrivateClient:
    pages = iter(responses)
    return KrakenPrivateClient(
        api_key="test-key",
        api_secret=SECRET,
        max_retries=0,
        opener=lambda request, timeout: Response(next(pages)),
    )


def market_client(*responses: object) -> KrakenPublicMarketClient:
    pages = iter(responses)
    return KrakenPublicMarketClient(
        opener=lambda request, timeout: Response(next(pages)), now=lambda: NOW
    )


def test_balance_ex_parses_exact_available_and_asset_kinds() -> None:
    snapshot = private_client(
        {
            "error": [],
            "result": {
                "XXBT": {
                    "balance": "2.0000000000000000001",
                    "credit": "0.2",
                    "credit_used": "0.05",
                    "hold_trade": "0.15",
                    "note": "provider detail",
                },
                "XBT.B": {"balance": "0.4"},
                "ETH.F": {"balance": "-0.1", "credit": "0.1"},
                "ADA.S": {"balance": "0"},
                "DOT.M": {"balance": "1"},
                "?": {"balance": "1"},
            },
        }
    ).extended_balance()

    btc = next(item for item in snapshot.balances if item.provider_asset_code == "XXBT")
    assert btc.canonical_asset == "BTC"
    assert btc.calculated_available == Decimal("2.0000000000000000001")
    assert btc.balance_kind == "spot"
    assert btc.provider_metadata == {"note": "provider detail"}
    extensions = {
        item.provider_asset_code: item.extension
        for item in snapshot.balances
        if item.extension is not None
    }
    assert extensions == {"ADA.S": "S", "DOT.M": "M", "ETH.F": "F", "XBT.B": "B"}
    assert "KRAKEN_NON_SPOT_BALANCE_PRESENT" in snapshot.warnings
    assert "KRAKEN_UNKNOWN_ASSET_CODE" in snapshot.warnings


@pytest.mark.parametrize(
    ("errors", "code"),
    [
        (["EGeneral:Permission denied"], "kraken_balance_permission_missing"),
        (["EAPI:Invalid key"], "kraken_authentication_failed"),
        (["EAPI:Rate limit exceeded"], "kraken_rate_limited"),
        (["EGeneral:bad request"], "kraken_api_error"),
    ],
)
def test_balance_ex_classifies_expected_provider_errors(
    errors: list[str], code: str
) -> None:
    with pytest.raises(KrakenPrivateError) as raised:
        private_client({"error": errors, "result": {}}).extended_balance()
    assert raised.value.code == code


@pytest.mark.parametrize(
    "result",
    [
        {"XXBT": "invalid"},
        {"XXBT": {"balance": "NaN"}},
        {"XXBT": {"balance": "malformed"}},
    ],
)
def test_balance_ex_rejects_invalid_provider_responses(result: object) -> None:
    with pytest.raises(KrakenPrivateError) as raised:
        private_client({"error": [], "result": result}).extended_balance()
    assert raised.value.code == "kraken_invalid_response"


def test_market_quote_resolves_btc_xbt_eur_and_selects_best_bid() -> None:
    quote = market_client(
        {
            "error": [],
            "result": {
                "XXBTZEUR": {
                    "altname": "XBTEUR",
                    "base": "XXBT",
                    "quote": "ZEUR",
                    "status": "online",
                },
                "XXBTZUSD": {
                    "altname": "XBTUSD",
                    "base": "XXBT",
                    "quote": "ZUSD",
                },
            },
        },
        {
            "error": [],
            "result": {
                "XXBTZEUR": {
                    "b": ["51000.123456789", "1", "1"],
                    "a": ["51001", "1", "1"],
                    "c": ["50999", "1"],
                }
            },
        },
    ).eur_quote("BTC")

    assert quote.pair == "XXBTZEUR"
    assert quote.best_bid_eur == Decimal("51000.123456789")
    assert quote.best_ask_eur == Decimal("51001")
    assert quote.last_trade_eur == Decimal("50999")
    assert quote.selected_reference_price_eur == quote.best_bid_eur
    assert quote.selected_reference == "KRAKEN_BEST_BID"
    assert quote.execution_price_guaranteed is False
    assert quote.fetched_at == NOW


def test_market_quote_reports_missing_pair_and_malformed_ticker() -> None:
    with pytest.raises(KrakenMarketError) as missing:
        market_client(
            {
                "error": [],
                "result": {
                    "ADAUSD": {"base": "ADA", "quote": "ZUSD"},
                    "ADA.FEUR": {"base": "ADA.F", "quote": "ZEUR"},
                },
            }
        ).eur_quote("ADA")
    assert missing.value.code == "kraken_eur_pair_unavailable"

    with pytest.raises(KrakenMarketError) as malformed:
        market_client(
            {
                "error": [],
                "result": {"ADAEUR": {"base": "ADA", "quote": "ZEUR"}},
            },
            {"error": [], "result": {"ADAEUR": {"b": [], "a": ["1"], "c": ["1"]}}},
        ).eur_quote("ADA")
    assert malformed.value.code == "kraken_invalid_response"


def test_public_market_client_classifies_transport_and_provider_failures() -> None:
    def http_error(request: Request, timeout: float) -> Response:
        raise HTTPError(request.full_url, 429, "limited", {}, BytesIO())

    with pytest.raises(KrakenMarketError) as limited:
        KrakenPublicMarketClient(opener=http_error).eur_quote("BTC")
    assert limited.value.code == "kraken_rate_limited"

    def unavailable(request: Request, timeout: float) -> Response:
        raise URLError("offline")

    with pytest.raises(KrakenMarketError) as offline:
        KrakenPublicMarketClient(opener=unavailable).eur_quote("BTC")
    assert offline.value.code == "kraken_unavailable"

    with pytest.raises(KrakenMarketError) as provider:
        market_client({"error": ["EGeneral:bad"], "result": {}}).eur_quote("BTC")
    assert provider.value.code == "kraken_api_error"


def test_market_client_validates_configuration_and_root_response() -> None:
    with pytest.raises(KrakenMarketError, match="Basisadresse"):
        KrakenPublicMarketClient(base_url="http://example.invalid")
    with pytest.raises(KrakenMarketError, match="Timeout"):
        KrakenPublicMarketClient(timeout=0)
    with pytest.raises(KrakenMarketError) as invalid:
        market_client([]).eur_quote("BTC")
    assert invalid.value.code == "kraken_invalid_response"


def test_default_market_opener_validates_urlopen_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(kraken_market, "urlopen", lambda request, timeout: object())
    with pytest.raises(KrakenMarketError) as invalid:
        kraken_market._open_url(Request("https://example.test"), 1)
    assert invalid.value.code == "kraken_invalid_response"

    expected = Response({"error": [], "result": {}})
    monkeypatch.setattr(kraken_market, "urlopen", lambda request, timeout: expected)
    assert kraken_market._open_url(Request("https://example.test"), 1) is expected


@pytest.mark.parametrize(
    ("response", "code"),
    [
        ({"error": ["EAPI:Rate limit exceeded"], "result": {}}, "kraken_rate_limited"),
        ({"error": [], "result": []}, "kraken_invalid_response"),
    ],
)
def test_market_client_rejects_rate_limit_and_invalid_result(
    response: object, code: str
) -> None:
    with pytest.raises(KrakenMarketError) as raised:
        market_client(response).eur_quote("BTC")
    assert raised.value.code == code


def test_market_client_classifies_non_rate_limit_http_error() -> None:
    def forbidden(request: Request, timeout: float) -> Response:
        raise HTTPError(request.full_url, 403, "forbidden", {}, BytesIO())

    with pytest.raises(KrakenMarketError) as raised:
        KrakenPublicMarketClient(opener=forbidden).eur_quote("BTC")
    assert raised.value.code == "kraken_unavailable"
    assert raised.value.temporary is False


def test_pair_resolution_ignores_malformed_metadata_and_uses_pair_key() -> None:
    quote = market_client(
        {
            "error": [],
            "result": {
                "scalar": "invalid",
                "missing": {"base": 1, "quote": "ZEUR"},
                "ADAOLD": {
                    "base": "ADA",
                    "quote": "ZEUR",
                    "status": "cancel_only",
                },
                "ADAEUR": {"base": "ADA", "quote": "ZEUR"},
            },
        },
        {
            "error": [],
            "result": {"ADAEUR": {"b": ["1"], "a": ["1.1"], "c": ["0.9"]}},
        },
    ).eur_quote("ADA")
    assert quote.pair == "ADAEUR"


@pytest.mark.parametrize(
    "ticker",
    [
        {},
        {"ONE": {}, "TWO": {}},
        {"ADAEUR": "invalid"},
        {"ADAEUR": {"b": ["0"], "a": ["1"], "c": ["1"]}},
    ],
)
def test_market_client_rejects_invalid_ticker_shapes(ticker: object) -> None:
    with pytest.raises(KrakenMarketError) as raised:
        market_client(
            {
                "error": [],
                "result": {"ADAEUR": {"base": "ADA", "quote": "ZEUR"}},
            },
            {"error": [], "result": ticker},
        ).eur_quote("ADA")
    assert raised.value.code == "kraken_invalid_response"
