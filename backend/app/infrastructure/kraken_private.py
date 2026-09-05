import base64
import binascii
import hashlib
import hmac
import json
import math
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class KrakenPrivateError(Exception):
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
        raise KrakenPrivateError(
            "kraken_invalid_response", "Kraken lieferte eine ungültige HTTP-Antwort."
        )
    return response


class MonotonicNonce:
    def __init__(self, clock_ms: Callable[[], int] | None = None) -> None:
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._last = 0
        self._lock = threading.Lock()

    def next(self) -> str:
        with self._lock:
            candidate = self._clock_ms()
            self._last = max(candidate, self._last + 1)
            return str(self._last)


_PROCESS_NONCE = MonotonicNonce()


def kraken_signature(path: str, form_data: Mapping[str, str], secret: str) -> str:
    try:
        secret_bytes = base64.b64decode(secret, validate=True)
    except (ValueError, TypeError, binascii.Error) as error:
        raise KrakenPrivateError(
            "kraken_configuration_invalid",
            "Das konfigurierte Kraken-API-Secret ist ungültig.",
        ) from error
    nonce = form_data.get("nonce")
    if not nonce:
        raise KrakenPrivateError(
            "kraken_request_invalid", "Für die private Anfrage fehlt die Nonce."
        )
    encoded = urlencode(form_data).encode("ascii")
    digest = hashlib.sha256(nonce.encode("ascii") + encoded).digest()
    return base64.b64encode(
        hmac.new(secret_bytes, path.encode("ascii") + digest, hashlib.sha512).digest()
    ).decode("ascii")


@dataclass(frozen=True)
class LedgerEntry:
    ledger_id: str
    occurred_at: datetime
    entry_type: str
    subtype: str
    asset: str
    amount: Decimal
    fee: Decimal
    extra: Mapping[str, object]


@dataclass(frozen=True)
class LedgerDiagnosticEntry:
    ledger_id: str
    occurred_at: datetime
    entry_type: str
    subtype: str
    asset: str


@dataclass(frozen=True)
class LedgerPreview:
    requested_start: datetime | None
    requested_end: datetime | None
    fetched_pages: int
    reported_total: int
    received_total: int
    unique_total: int
    duplicate_ids: tuple[str, ...]
    conflicting_duplicate_ids: tuple[str, ...]
    earliest_entry_at: datetime | None
    latest_entry_at: datetime | None
    counts_by_type: Mapping[str, int]
    counts_by_subtype: Mapping[str, int]
    counts_by_asset: Mapping[str, int]
    unknown_types: tuple[str, ...]
    unknown_subtypes: tuple[str, ...]
    malformed_entries: int
    pagination_complete: bool
    stable_ledger_id_digest: str
    warnings: tuple[str, ...]
    ready_for_import: bool
    diagnostics: tuple[LedgerDiagnosticEntry, ...]
    records: tuple[LedgerEntry, ...]


_KNOWN_TYPES = {
    "adjustment",
    "credit",
    "deposit",
    "dividend",
    "margin",
    "rollover",
    "sale",
    "settled",
    "staking",
    "trade",
    "transfer",
    "withdrawal",
    "nft_rebate",
}
_KNOWN_SUBTYPES = {
    "",
    "migration",
    "onchain",
    "reward",
    "spotfromfutures",
    "spottostaking",
    "stakingfromspot",
    "stakingtospot",
}


class KrakenPrivateClient:
    ledger_path = "/0/private/Ledgers"

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str = "https://api.kraken.com",
        timeout: int = 15,
        max_retries: int = 2,
        max_pages: int = 10_000,
        ledger_min_interval_seconds: float = 9,
        rate_limit_retry_base_seconds: float = 30,
        clock: Callable[[], float] = time.monotonic,
        nonce: MonotonicNonce | None = None,
        opener: OpenRequest = _open_url,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key or not api_secret:
            raise KrakenPrivateError(
                "kraken_not_configured",
                "Kraken-Lesezugriff ist serverseitig nicht konfiguriert.",
            )
        if not base_url.startswith("https://") and not base_url.startswith(
            ("http://127.0.0.1", "http://localhost")
        ):
            raise KrakenPrivateError(
                "kraken_base_url_invalid",
                "Die Kraken-API-Basisadresse ist nicht zulässig.",
            )
        if timeout <= 0 or max_retries < 0 or max_pages <= 0:
            raise KrakenPrivateError(
                "kraken_configuration_invalid",
                "Timeout und Wiederholungsanzahl sind ungültig.",
            )
        if not (0 < ledger_min_interval_seconds <= 300) or not (
            30 <= rate_limit_retry_base_seconds <= 3600
        ):
            raise KrakenPrivateError(
                "kraken_configuration_invalid", "Kraken-Wartezeiten sind ungültig."
            )
        self._clock = clock
        self._ledger_interval = ledger_min_interval_seconds
        self._rate_limit_base = rate_limit_retry_base_seconds
        self._last_ledger_start: float | None = None
        self._api_key = api_key
        self._api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_pages = max_pages
        self._nonce = nonce or _PROCESS_NONCE
        self._opener = opener
        self._sleeper = sleeper

    def _private_post(
        self, path: str, fields: Mapping[str, str]
    ) -> Mapping[str, object]:
        attempt = 0
        while True:
            if path == self.ledger_path:
                if self._last_ledger_start is not None:
                    remaining = self._ledger_interval - (
                        self._clock() - self._last_ledger_start
                    )
                    if remaining > 0:
                        self._sleeper(remaining)
                self._last_ledger_start = self._clock()
            data = dict(fields)
            data["nonce"] = self._nonce.next()
            encoded = urlencode(data).encode("ascii")
            request = Request(
                self.base_url + path,
                data=encoded,
                headers={
                    "API-Key": self._api_key,
                    "API-Sign": kraken_signature(path, data, self._api_secret),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                method="POST",
            )
            try:
                with self._opener(request, float(self.timeout)) as response:
                    raw = response.read()
                parsed = json.loads(raw, parse_float=Decimal, parse_int=Decimal)
                if not isinstance(parsed, dict):
                    raise ValueError("root")
                errors = parsed.get("error")
                if not isinstance(errors, list):
                    raise ValueError("error")
                if errors:
                    self._raise_api_error(errors)
                result = parsed.get("result")
                if not isinstance(result, dict):
                    raise ValueError("result")
                return {str(key): value for key, value in result.items()}
            except HTTPError as error:
                if error.code == 429:
                    provider_error = KrakenPrivateError(
                        "kraken_rate_limited",
                        "Kraken begrenzt derzeit die Anfragerate.",
                        temporary=True,
                    )
                elif error.code in {401, 403}:
                    provider_error = KrakenPrivateError(
                        "kraken_authentication_failed",
                        "Kraken hat die Zugangsdaten abgelehnt.",
                    )
                else:
                    provider_error = KrakenPrivateError(
                        "kraken_unavailable",
                        "Kraken ist vorübergehend nicht erreichbar.",
                        temporary=error.code >= 500,
                    )
                if provider_error.temporary and attempt < self.max_retries:
                    delay = self._retry_delay(provider_error, attempt)
                    if error.code == 429:
                        delay = max(
                            delay, self._retry_after(error.headers.get("Retry-After"))
                        )
                    self._sleeper(delay)
                    attempt += 1
                    continue
                raise provider_error from error
            except TimeoutError as error:
                if attempt < self.max_retries:
                    self._sleeper(min(2**attempt, 4))
                    attempt += 1
                    continue
                raise KrakenPrivateError(
                    "kraken_timeout", "Zeitüberschreitung bei Kraken.", temporary=True
                ) from error
            except URLError as error:
                if attempt < self.max_retries:
                    self._sleeper(min(2**attempt, 4))
                    attempt += 1
                    continue
                raise KrakenPrivateError(
                    "kraken_unavailable",
                    "Kraken ist vorübergehend nicht erreichbar.",
                    temporary=True,
                ) from error
            except KrakenPrivateError as error:
                if error.temporary and attempt < self.max_retries:
                    self._sleeper(self._retry_delay(error, attempt))
                    attempt += 1
                    continue
                raise
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
                raise KrakenPrivateError(
                    "kraken_invalid_response", "Kraken lieferte eine ungültige Antwort."
                ) from error

    def _retry_delay(self, error: KrakenPrivateError, attempt: int) -> float:
        backoff_factor = float(2**attempt)
        if error.code == "kraken_rate_limited":
            return self._rate_limit_base * backoff_factor
        return min(backoff_factor, 4.0)

    @staticmethod
    def _retry_after(value: str | None) -> float:
        if value is None:
            return 0
        try:
            seconds = float(value)
        except ValueError:
            try:
                seconds = (
                    parsedate_to_datetime(value) - datetime.now(UTC)
                ).total_seconds()
            except (ValueError, TypeError, OverflowError):
                return 0
        return max(0, seconds) if math.isfinite(seconds) else 0

    def check_ledger_access(self) -> None:
        result = self._private_post(self.ledger_path, {"type": "all", "ofs": "0"})
        if not isinstance(result.get("ledger"), dict) or not isinstance(
            result.get("count"), (int, Decimal)
        ):
            raise KrakenPrivateError(
                "kraken_invalid_response", "Kraken lieferte ungültige Ledgerdaten."
            )

    @staticmethod
    def _raise_api_error(errors: list[object]) -> None:
        labels = " ".join(item for item in errors if isinstance(item, str)).lower()
        if "invalid key" in labels or "invalid signature" in labels:
            code, message = (
                "kraken_authentication_failed",
                "Kraken hat die Zugangsdaten abgelehnt.",
            )
        elif "permission denied" in labels:
            code, message = (
                "kraken_ledger_permission_missing",
                "Dem Kraken-Schlüssel fehlt die Ledger-Leseberechtigung.",
            )
        elif "invalid nonce" in labels:
            code, message = "kraken_invalid_nonce", "Kraken hat die Nonce abgelehnt."
        elif "rate limit" in labels or "too many requests" in labels:
            code, message = (
                "kraken_rate_limited",
                "Kraken begrenzt derzeit die Anfragerate.",
            )
        else:
            code, message = (
                "kraken_api_error",
                "Kraken konnte die Ledger-Anfrage nicht verarbeiten.",
            )
        raise KrakenPrivateError(code, message, temporary=code == "kraken_rate_limited")

    def ledger_preview(
        self,
        *,
        start: datetime | None,
        end: datetime | None,
        asset: str | None,
        ledger_type: str,
        diagnostic_limit: int,
    ) -> LedgerPreview:
        if start and (start.tzinfo is None or start.utcoffset() is None):
            raise ValueError("start must be timezone-aware")
        if end and (end.tzinfo is None or end.utcoffset() is None):
            raise ValueError("end must be timezone-aware")
        if start and end and end <= start:
            raise ValueError("end must be after start")
        if diagnostic_limit < 0 or diagnostic_limit > 100:
            raise ValueError("diagnostic_limit must be between 0 and 100")
        fields = {"type": ledger_type or "all", "ofs": "0"}
        if start:
            fields["start"] = str(self._unix_seconds(start) - 1)
        if end:
            fields["end"] = str(self._unix_seconds(end) + (1 if end.microsecond else 0))
        if asset:
            fields["asset"] = asset
        offset = 0
        pages = 0
        reported_total = 0
        malformed = 0
        entries: dict[str, LedgerEntry] = {}
        duplicates: set[str] = set()
        conflicts: set[str] = set()
        complete = False
        warnings: list[str] = []
        while pages < self.max_pages:
            fields["ofs"] = str(offset)
            result = self._private_post(self.ledger_path, fields)
            count = result.get("count")
            ledger = result.get("ledger")
            if (
                not isinstance(count, (int, Decimal))
                or Decimal(count) != Decimal(count).to_integral_value()
                or count < 0
                or not isinstance(ledger, dict)
            ):
                raise KrakenPrivateError(
                    "kraken_invalid_response", "Kraken lieferte ungültige Ledgerdaten."
                )
            reported_total = int(count)
            pages += 1
            page_size = len(ledger)
            for ledger_id, raw_entry in ledger.items():
                if not isinstance(ledger_id, str) or not isinstance(raw_entry, dict):
                    malformed += 1
                    continue
                try:
                    entry = self._parse_entry(ledger_id, raw_entry)
                except (KeyError, TypeError, ValueError, InvalidOperation):
                    malformed += 1
                    continue
                previous = entries.get(ledger_id)
                if previous is not None:
                    duplicates.add(ledger_id)
                    if previous != entry:
                        conflicts.add(ledger_id)
                else:
                    entries[ledger_id] = entry
            if offset + page_size >= reported_total:
                complete = True
                break
            if page_size == 0:
                warnings.append("Die Pagination meldete keinen Fortschritt.")
                break
            offset += page_size
        if not complete and pages >= self.max_pages:
            warnings.append("Die Sicherheitsgrenze der Pagination wurde erreicht.")
        ordered_all = sorted(
            entries.values(), key=lambda item: (item.occurred_at, item.ledger_id)
        )
        provider_count_matches = reported_total == len(entries)
        if not provider_count_matches:
            warnings.append(
                "Gemeldete Gesamtzahl und empfangene Datensätze weichen ab."
            )
        ordered = [
            item
            for item in ordered_all
            if (start is None or start <= item.occurred_at)
            and (end is None or item.occurred_at < end)
        ]
        filtered_ids = {item.ledger_id for item in ordered}
        duplicates.intersection_update(filtered_ids)
        conflicts.intersection_update(filtered_ids)
        digest = hashlib.sha256(
            "\n".join(sorted(item.ledger_id for item in ordered)).encode("utf-8")
        ).hexdigest()
        counts_type = self._counts(item.entry_type for item in ordered)
        counts_subtype = self._counts(item.subtype for item in ordered)
        counts_asset = self._counts(item.asset for item in ordered)
        unknown_types = tuple(sorted(set(counts_type) - _KNOWN_TYPES))
        unknown_subtypes = tuple(sorted(set(counts_subtype) - _KNOWN_SUBTYPES))
        ready = complete and not conflicts and malformed == 0 and provider_count_matches
        return LedgerPreview(
            requested_start=start.astimezone(UTC) if start else None,
            requested_end=end.astimezone(UTC) if end else None,
            fetched_pages=pages,
            reported_total=len(ordered),
            received_total=len(ordered) + len(duplicates),
            unique_total=len(ordered),
            duplicate_ids=tuple(sorted(duplicates)),
            conflicting_duplicate_ids=tuple(sorted(conflicts)),
            earliest_entry_at=ordered[0].occurred_at if ordered else None,
            latest_entry_at=ordered[-1].occurred_at if ordered else None,
            counts_by_type=counts_type,
            counts_by_subtype=counts_subtype,
            counts_by_asset=counts_asset,
            unknown_types=unknown_types,
            unknown_subtypes=unknown_subtypes,
            malformed_entries=malformed,
            pagination_complete=complete,
            stable_ledger_id_digest=digest,
            warnings=tuple(warnings),
            ready_for_import=ready,
            diagnostics=tuple(
                LedgerDiagnosticEntry(
                    item.ledger_id,
                    item.occurred_at,
                    item.entry_type,
                    item.subtype,
                    item.asset,
                )
                for item in ordered[:diagnostic_limit]
            ),
            records=tuple(ordered),
        )

    @staticmethod
    def _parse_entry(ledger_id: str, raw: Mapping[object, object]) -> LedgerEntry:
        timestamp = Decimal(str(raw["time"]))
        if not timestamp.is_finite():
            raise ValueError("Ledger timestamp must be finite")
        seconds = int(timestamp)
        microseconds = int((timestamp - seconds) * Decimal("1000000"))
        occurred = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
            seconds=seconds, microseconds=microseconds
        )
        entry_type = raw["type"]
        asset = raw["asset"]
        if not isinstance(entry_type, str) or not isinstance(asset, str):
            raise TypeError("Ledger type and asset must be strings")
        subtype_value = raw.get("subtype", "")
        if not isinstance(subtype_value, str):
            raise TypeError("Ledger subtype must be a string")
        amount = Decimal(str(raw["amount"]))
        fee = Decimal(str(raw["fee"]))
        if not amount.is_finite() or not fee.is_finite():
            raise ValueError("Ledger decimals must be finite")
        refid = raw["refid"]
        if not isinstance(refid, str):
            raise TypeError("Ledger reference must be a string")
        balance_value = raw.get("balance")
        if balance_value not in {None, ""}:
            balance = Decimal(str(balance_value))
            if not balance.is_finite():
                raise ValueError("Ledger balance must be finite")
        known = {"time", "type", "subtype", "asset", "amount", "fee"}
        extra = {str(key): value for key, value in raw.items() if key not in known}
        return LedgerEntry(
            ledger_id=ledger_id,
            occurred_at=occurred,
            entry_type=entry_type,
            subtype=subtype_value,
            asset=asset,
            amount=amount,
            fee=fee,
            extra=extra,
        )

    @staticmethod
    def _unix_seconds(value: datetime) -> int:
        delta = value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
        return delta.days * 86_400 + delta.seconds

    @staticmethod
    def _counts(values: Iterable[str]) -> dict[str, int]:
        result: dict[str, int] = {}
        for value in values:
            result[value] = result.get(value, 0) + 1
        return result
