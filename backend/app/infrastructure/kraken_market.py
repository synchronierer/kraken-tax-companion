import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.adapters.kraken.assets import normalize_kraken_asset


class KrakenMarketError(Exception):
    def __init__(self, code: str, message: str, *, temporary: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.temporary = temporary


@runtime_checkable
class _Response(Protocol):
    def __enter__(self) -> "_Response": ...

    def __exit__(self, *args: object) -> None: ...

    def read(self) -> bytes: ...


OpenRequest = Callable[[Request, float], _Response]


def _open_url(request: Request, timeout: float) -> _Response:
    response: object = urlopen(request, timeout=timeout)
    if not isinstance(response, _Response):
        raise KrakenMarketError(
            "kraken_invalid_response", "Kraken lieferte eine ungültige HTTP-Antwort."
        )
    return response


@dataclass(frozen=True, kw_only=True)
class KrakenMarketQuote:
    asset: str
    quote_asset: str
    pair: str
    best_bid_eur: Decimal
    best_ask_eur: Decimal
    last_trade_eur: Decimal
    selected_reference_price_eur: Decimal
    selected_reference: str
    fetched_at: datetime
    execution_price_guaranteed: bool = False


class KrakenPublicMarketClient:
    asset_pairs_path = "/0/public/AssetPairs"
    ticker_path = "/0/public/Ticker"

    def __init__(
        self,
        *,
        base_url: str = "https://api.kraken.com",
        timeout: int = 15,
        opener: OpenRequest = _open_url,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not base_url.startswith("https://") and not base_url.startswith(
            ("http://127.0.0.1", "http://localhost")
        ):
            raise KrakenMarketError(
                "kraken_base_url_invalid",
                "Die Kraken-API-Basisadresse ist nicht zulässig.",
            )
        if timeout <= 0:
            raise KrakenMarketError(
                "kraken_configuration_invalid", "Das Kraken-Timeout ist ungültig."
            )
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = opener
        self._now = now

    def _get(
        self, path: str, fields: Mapping[str, str] | None = None
    ) -> dict[str, object]:
        query = f"?{urlencode(fields)}" if fields else ""
        request = Request(self.base_url + path + query, method="GET")
        try:
            with self._opener(request, float(self.timeout)) as response:
                parsed = json.loads(
                    response.read(), parse_float=Decimal, parse_int=Decimal
                )
            if not isinstance(parsed, dict) or not isinstance(
                parsed.get("error"), list
            ):
                raise ValueError("root")
            errors = parsed["error"]
            if errors:
                labels = " ".join(
                    item for item in errors if isinstance(item, str)
                ).lower()
                if "rate limit" in labels or "too many requests" in labels:
                    raise KrakenMarketError(
                        "kraken_rate_limited",
                        "Kraken begrenzt derzeit die Anfragerate.",
                        temporary=True,
                    )
                raise KrakenMarketError(
                    "kraken_api_error",
                    "Kraken konnte die Marktdatenanfrage nicht verarbeiten.",
                )
            result = parsed.get("result")
            if not isinstance(result, dict):
                raise ValueError("result")
            return {str(key): value for key, value in result.items()}
        except HTTPError as error:
            if error.code == 429:
                raise KrakenMarketError(
                    "kraken_rate_limited",
                    "Kraken begrenzt derzeit die Anfragerate.",
                    temporary=True,
                ) from error
            raise KrakenMarketError(
                "kraken_unavailable",
                "Kraken ist vorübergehend nicht erreichbar.",
                temporary=error.code >= 500,
            ) from error
        except (TimeoutError, URLError) as error:
            raise KrakenMarketError(
                "kraken_unavailable",
                "Kraken ist vorübergehend nicht erreichbar.",
                temporary=True,
            ) from error
        except KrakenMarketError:
            raise
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
            raise KrakenMarketError(
                "kraken_invalid_response", "Kraken lieferte ungültige Marktdaten."
            ) from error

    def eur_quote(self, asset: str) -> KrakenMarketQuote:
        pairs = self._get(self.asset_pairs_path)
        candidates: list[tuple[str, str]] = []
        for pair_key, raw in pairs.items():
            if not isinstance(raw, dict):
                continue
            base = raw.get("base")
            quote = raw.get("quote")
            if not isinstance(base, str) or not isinstance(quote, str):
                continue
            base_identity = normalize_kraken_asset(base)
            quote_identity = normalize_kraken_asset(quote)
            if (
                base_identity.normalized_asset == asset.upper()
                and quote_identity.normalized_asset == "EUR"
                and base_identity.product_variant is None
                and raw.get("status", "online") == "online"
            ):
                altname = raw.get("altname")
                candidates.append(
                    (pair_key, altname if isinstance(altname, str) else pair_key)
                )
        if not candidates:
            raise KrakenMarketError(
                "kraken_eur_pair_unavailable",
                "Für das Asset ist kein direktes Kraken-EUR-Paar verfügbar.",
            )
        pair_key, query_pair = sorted(candidates)[0]
        ticker = self._get(self.ticker_path, {"pair": query_pair})
        if len(ticker) != 1:
            raise KrakenMarketError(
                "kraken_invalid_response", "Kraken lieferte ungültige Ticker-Daten."
            )
        raw_ticker = next(iter(ticker.values()))
        if not isinstance(raw_ticker, dict):
            raise KrakenMarketError(
                "kraken_invalid_response", "Kraken lieferte ungültige Ticker-Daten."
            )
        try:
            bid = Decimal(str(raw_ticker["b"][0]))
            ask = Decimal(str(raw_ticker["a"][0]))
            last = Decimal(str(raw_ticker["c"][0]))
            if not all(value.is_finite() and value > 0 for value in (bid, ask, last)):
                raise ValueError("invalid ticker decimal")
        except (KeyError, IndexError, TypeError, InvalidOperation, ValueError) as error:
            raise KrakenMarketError(
                "kraken_invalid_response", "Kraken lieferte ungültige Ticker-Daten."
            ) from error
        return KrakenMarketQuote(
            asset=asset.upper(),
            quote_asset="EUR",
            pair=pair_key,
            best_bid_eur=bid,
            best_ask_eur=ask,
            last_trade_eur=last,
            selected_reference_price_eur=bid,
            selected_reference="KRAKEN_BEST_BID",
            fetched_at=self._now().astimezone(UTC),
        )
