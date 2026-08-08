import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.core.time import require_utc
from app.core.valuation import PriceObservation, PriceProviderError

ASSET_IDS = {
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
MAPPING_VERSION = "coingecko-asset-map-v2"


class CoinGeckoProvider:
    name = "coingecko"
    contract_version = "market-chart-range-v1"

    def __init__(
        self,
        *,
        base_url: str,
        mode: str,
        api_key: str | None,
        timeout_seconds: int,
        retries: int = 2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.mode = mode
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        if mode not in {"keyless", "demo", "pro", "disabled"}:
            raise ValueError("Unsupported CoinGecko mode.")
        if mode in {"demo", "pro"} and not api_key:
            raise ValueError("The configured CoinGecko mode requires an API key.")
        if retries < 0:
            raise ValueError("Provider retries must not be negative.")

    def observations(
        self, asset: str, target_currency: str, start: datetime, end: datetime
    ) -> tuple[PriceObservation, ...]:
        start = require_utc(start)
        end = require_utc(end)
        if end <= start:
            raise ValueError("Provider interval must have a positive duration.")
        if self.mode == "disabled":
            raise PriceProviderError(
                "valuation_provider_disabled", "Die Kursquelle ist deaktiviert."
            )
        asset_id = ASSET_IDS.get(asset.upper())
        if asset_id is None:
            raise PriceProviderError(
                "valuation_asset_mapping_missing",
                f"Für {asset} existiert kein explizites Mapping ({MAPPING_VERSION}).",
            )
        by_time: dict[datetime, PriceObservation] = {}
        cursor = start
        while cursor < end:
            window_end = min(end, cursor + timedelta(days=90))
            for observation in self._window(
                asset_id, target_currency, cursor, window_end
            ):
                by_time[observation.observed_at] = observation
            cursor = window_end
        return tuple(by_time[key] for key in sorted(by_time))

    def _window(
        self,
        asset_id: str,
        target_currency: str,
        start: datetime,
        end: datetime,
    ) -> tuple[PriceObservation, ...]:
        query = urlencode(
            {
                "vs_currency": target_currency.lower(),
                "from": int(start.timestamp()),
                "to": int(end.timestamp()),
            }
        )
        request = Request(
            f"{self.base_url}/coins/{asset_id}/market_chart/range?{query}",
            headers=self._headers(),
        )
        payload = self._request(request)
        prices = payload.get("prices")
        if not isinstance(prices, list):
            raise PriceProviderError(
                "valuation_provider_invalid_response",
                "Die Providerantwort enthält keine gültige Preisliste.",
            )
        by_time: dict[datetime, PriceObservation] = {}
        for row in prices:
            if not isinstance(row, list) or len(row) < 2:
                raise PriceProviderError(
                    "valuation_provider_invalid_response",
                    "Ein Preispunkt ist ungültig.",
                )
            milliseconds = Decimal(str(row[0]))
            if milliseconds != milliseconds.to_integral_value():
                raise PriceProviderError(
                    "valuation_provider_invalid_response",
                    "Ein Providerzeitpunkt ist ungültig.",
                )
            observed = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
                milliseconds=int(milliseconds)
            )
            if start <= observed <= end:
                by_time[observed] = PriceObservation(
                    observed_at=observed, price_eur=Decimal(str(row[1]))
                )
        return tuple(by_time[key] for key in sorted(by_time))

    @staticmethod
    def asset_id(asset: str) -> str:
        value = ASSET_IDS.get(asset.upper())
        if value is None:
            raise PriceProviderError(
                "valuation_asset_mapping_missing",
                f"Asset-Mapping fehlt ({MAPPING_VERSION}).",
            )
        return value

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.mode == "demo":
            headers["x-cg-demo-api-key"] = self._api_key or ""
        elif self.mode == "pro":
            headers["x-cg-pro-api-key"] = self._api_key or ""
        return headers

    def _request(self, request: Request) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    raw = response.read()
                parsed = json.loads(raw, parse_float=Decimal, parse_int=Decimal)
                if not isinstance(parsed, dict):
                    raise ValueError
                return parsed
            except HTTPError as error:
                code = {
                    401: "valuation_provider_unauthorized",
                    403: "valuation_provider_unauthorized",
                    404: "valuation_no_price_data",
                    429: "valuation_provider_rate_limited",
                }.get(error.code, "valuation_provider_unavailable")
                temporary = error.code == 429 or error.code >= 500
                if temporary and attempt < self.retries:
                    retry_after = error.headers.get("Retry-After")
                    time.sleep(self._retry_delay(attempt, retry_after))
                    attempt += 1
                    continue
                raise PriceProviderError(
                    code, "Kursprovider-Anfrage fehlgeschlagen.", temporary=temporary
                ) from error
            except TimeoutError as error:
                if attempt < self.retries:
                    time.sleep(min(2**attempt, 4))
                    attempt += 1
                    continue
                raise PriceProviderError(
                    "valuation_provider_timeout",
                    "Zeitüberschreitung beim Kursprovider.",
                    temporary=True,
                ) from error
            except URLError as error:
                if attempt < self.retries:
                    time.sleep(min(2**attempt, 4))
                    attempt += 1
                    continue
                raise PriceProviderError(
                    "valuation_provider_unavailable",
                    "Kursprovider ist vorübergehend nicht erreichbar.",
                    temporary=True,
                ) from error
            except (json.JSONDecodeError, ValueError) as error:
                raise PriceProviderError(
                    "valuation_provider_invalid_response",
                    "Ungültige Antwort des Kursproviders.",
                ) from error

    @staticmethod
    def _retry_delay(attempt: int, retry_after: str | None) -> float:
        fallback = float(min(2**attempt, 4))
        if not retry_after:
            return fallback
        if retry_after.isdigit():
            return float(min(int(retry_after), 30))
        try:
            parsed = parsedate_to_datetime(retry_after)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            seconds = (parsed - datetime.now(UTC)).total_seconds()
            return max(0.0, min(seconds, 30.0))
        except (TypeError, ValueError, OverflowError):
            return fallback
