import base64
import inspect
import json
import threading
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs
from urllib.request import Request

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.adapters.kraken.assets import KrakenAssetNormalizationKind
from app.adapters.kraken.ledger import (
    ParsedLedgerBatch,
    canonical_from_api,
    compare_ledgers,
    filter_records,
    ledger_digest,
    normalize_asset,
    parse_ledger_csv,
)
from app.api import kraken_live
from app.config.settings import Settings, get_settings
from app.database.base import Base
from app.database.session import get_session
from app.infrastructure import kraken_private
from app.infrastructure.kraken_private import (
    KrakenPrivateClient,
    KrakenPrivateError,
    LedgerPreview,
    MonotonicNonce,
    kraken_signature,
)
from app.main import app

SECRET = base64.b64encode(b"synthetic-secret").decode("ascii")
START = datetime(2026, 1, 1, tzinfo=UTC)


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class RawResponse(FakeResponse):
    def __init__(self, payload: bytes) -> None:
        self.payload = payload


def entry(
    *,
    timestamp: str = "1767225600.125",
    entry_type: object = "trade",
    subtype: object = "",
    asset: object = "XXBT",
    amount: object = "1.25",
    fee: object = "0.01",
) -> dict[str, object]:
    return {
        "time": timestamp,
        "type": entry_type,
        "subtype": subtype,
        "asset": asset,
        "amount": amount,
        "fee": fee,
        "refid": "synthetic-reference",
    }


def client_with_pages(*pages: object, retries: int = 0) -> KrakenPrivateClient:
    iterator = iter(pages)

    def opener(_: Request, __: float) -> FakeResponse:
        value = next(iterator)
        if isinstance(value, Exception):
            raise value
        return FakeResponse(value)

    return KrakenPrivateClient(
        api_key="synthetic-key",
        api_secret=SECRET,
        max_retries=retries,
        nonce=MonotonicNonce(lambda: 1000),
        opener=opener,
        sleeper=lambda _: None,
    )


def preview(client: KrakenPrivateClient, limit: int = 10) -> LedgerPreview:
    return client.ledger_preview(
        start=START,
        end=datetime(2026, 1, 2, tzinfo=UTC),
        asset=None,
        ledger_type="all",
        diagnostic_limit=limit,
    )


def test_signature_nonce_and_configuration_contract() -> None:
    fields = {"nonce": "1616492376594", "asset": "XBT", "ofs": "0"}
    signature = kraken_signature("/0/private/Ledgers", fields, SECRET)
    assert (
        signature
        == "kLqETV/N0VyRHjme4N3nBzn3PnWtj4ypjFxJgDWfrYapxEb1QtCMHD7ywjH6QMrzXTHc"
        "clcANOOW6UUzb+bPiQ=="
    )
    assert signature != kraken_signature(
        "/0/private/Ledgers", fields | {"asset": "ETH"}, SECRET
    )
    with pytest.raises(KrakenPrivateError, match="Secret"):
        kraken_signature("/0/private/Ledgers", fields, "not base64!")
    with pytest.raises(KrakenPrivateError, match="Nonce"):
        kraken_signature("/0/private/Ledgers", {}, SECRET)

    nonce = MonotonicNonce(lambda: 42)
    assert [nonce.next(), nonce.next(), nonce.next()] == ["42", "43", "44"]
    with pytest.raises(KrakenPrivateError, match="nicht konfiguriert"):
        KrakenPrivateClient(api_key="", api_secret="")
    with pytest.raises(KrakenPrivateError, match="Basisadresse"):
        KrakenPrivateClient(
            api_key="key", api_secret=SECRET, base_url="http://remote.invalid"
        )
    with pytest.raises(KrakenPrivateError, match="Timeout"):
        KrakenPrivateClient(api_key="key", api_secret=SECRET, timeout=0)
    with pytest.raises(KrakenPrivateError, match="Timeout"):
        KrakenPrivateClient(api_key="key", api_secret=SECRET, max_pages=0)
    with pytest.raises(KrakenPrivateError, match="Timeout"):
        KrakenPrivateClient(api_key="key", api_secret=SECRET, max_retries=-1)

    process_nonces: list[int] = []

    def capture_nonce(request: Request, _: float) -> FakeResponse:
        assert request.data is not None
        process_nonces.append(int(parse_qs(request.data.decode())["nonce"][0]))
        return FakeResponse({"error": [], "result": {"ledger": {}, "count": 0}})

    for _ in range(2):
        KrakenPrivateClient(
            api_key="synthetic-key", api_secret=SECRET, opener=capture_nonce
        ).check_ledger_access()
    assert process_nonces[1] > process_nonces[0]


def test_private_request_success_retry_and_safe_errors() -> None:
    ok = {"error": [], "result": {"ledger": {}, "count": 0}}
    sleeps: list[float] = []
    attempts = iter([URLError("synthetic transport"), ok])

    def opener(request: Request, timeout: float) -> FakeResponse:
        assert request.full_url == "https://api.kraken.com/0/private/Ledgers"
        assert request.get_header("Api-key") == "synthetic-key"
        assert timeout == 15.0
        value = next(attempts)
        if isinstance(value, Exception):
            raise value
        return FakeResponse(value)

    provider = KrakenPrivateClient(
        api_key="synthetic-key",
        api_secret=SECRET,
        max_retries=1,
        opener=opener,
        sleeper=sleeps.append,
        clock=iter(range(0, 1000, 10)).__next__,
    )
    assert preview(provider).ready_for_import
    assert sleeps == [1]

    rate_attempts = iter(
        [
            {"error": ["EAPI:Rate limit exceeded"], "result": {}},
            ok,
        ]
    )
    rate_provider = KrakenPrivateClient(
        api_key="synthetic-key",
        api_secret=SECRET,
        max_retries=1,
        opener=lambda _request, _timeout: FakeResponse(next(rate_attempts)),
        sleeper=sleeps.append,
        clock=iter(range(0, 1000, 10)).__next__,
    )
    assert preview(rate_provider).ready_for_import

    server_attempts = iter([HTTPError("url", 503, "down", {}, None), ok])

    def retry_server(_: Request, __: float) -> FakeResponse:
        value = next(server_attempts)
        if isinstance(value, Exception):
            raise value
        return FakeResponse(value)

    server_provider = KrakenPrivateClient(
        api_key="synthetic-key",
        api_secret=SECRET,
        max_retries=1,
        opener=retry_server,
        sleeper=sleeps.append,
        clock=iter(range(0, 1000, 10)).__next__,
    )
    assert preview(server_provider).ready_for_import

    timeout_attempts = iter([TimeoutError(), ok])

    def retry_timeout(_: Request, __: float) -> FakeResponse:
        value = next(timeout_attempts)
        if isinstance(value, Exception):
            raise value
        return FakeResponse(value)

    timeout_provider = KrakenPrivateClient(
        api_key="synthetic-key",
        api_secret=SECRET,
        max_retries=1,
        opener=retry_timeout,
        sleeper=sleeps.append,
        clock=iter(range(0, 1000, 10)).__next__,
    )
    assert preview(timeout_provider).ready_for_import

    for error, code in [
        (TimeoutError(), "kraken_timeout"),
        (URLError("offline"), "kraken_unavailable"),
        (HTTPError("url", 429, "rate", {}, None), "kraken_rate_limited"),
        (HTTPError("url", 401, "auth", {}, None), "kraken_authentication_failed"),
        (HTTPError("url", 400, "bad", {}, None), "kraken_unavailable"),
        (HTTPError("url", 500, "down", {}, None), "kraken_unavailable"),
    ]:
        failing = client_with_pages(error)
        with pytest.raises(KrakenPrivateError) as caught:
            preview(failing)
        assert caught.value.code == code
        assert "synthetic-key" not in str(caught.value)
        assert SECRET not in str(caught.value)


@pytest.mark.parametrize(
    ("errors", "code"),
    [
        (["EAPI:Invalid key"], "kraken_authentication_failed"),
        (["EGeneral:Permission denied"], "kraken_ledger_permission_missing"),
        (["EAPI:Invalid nonce"], "kraken_invalid_nonce"),
        (["EAPI:Rate limit exceeded"], "kraken_rate_limited"),
        (["EGeneral:Unknown"], "kraken_api_error"),
    ],
)
def test_kraken_error_lists_are_safely_classified(errors: list[str], code: str) -> None:
    with pytest.raises(KrakenPrivateError) as caught:
        preview(client_with_pages({"error": errors, "result": {}}))
    assert caught.value.code == code
    assert errors[0] not in str(caught.value)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"result": {}},
        {"error": "not-a-list", "result": {}},
        {"error": [], "result": []},
        {"error": [], "result": {"ledger": [], "count": 0}},
        {"error": [], "result": {"ledger": {}, "count": "zero"}},
        {"error": [], "result": {"ledger": {}, "count": "1.5"}},
        {"error": [], "result": {"ledger": {}, "count": -1}},
    ],
)
def test_invalid_provider_responses_are_rejected(payload: object) -> None:
    with pytest.raises(KrakenPrivateError) as caught:
        preview(client_with_pages(payload))
    assert caught.value.code == "kraken_invalid_response"

    def invalid_json(_: Request, __: float) -> RawResponse:
        return RawResponse(b"not-json")

    provider = KrakenPrivateClient(
        api_key="synthetic-key", api_secret=SECRET, opener=invalid_json
    )
    with pytest.raises(KrakenPrivateError, match="ungültige Antwort"):
        preview(provider)

    invalid_access = client_with_pages(
        {"error": [], "result": {"ledger": [], "count": 0}}
    )
    with pytest.raises(KrakenPrivateError, match="Ledgerdaten"):
        invalid_access.check_ledger_access()


def test_invalid_http_response_object_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(kraken_private, "urlopen", lambda *_args, **_kwargs: object())
    with pytest.raises(KrakenPrivateError, match="HTTP-Antwort"):
        kraken_private._open_url(Request("https://api.kraken.com"), 1)


def test_pagination_deduplication_sorting_digest_and_unknown_values() -> None:
    first = {
        "error": [],
        "result": {
            "count": 3,
            "ledger": {
                "L2": entry(timestamp="1767225600", entry_type="future_type"),
                "L1": entry(timestamp="1767225600", subtype="future_subtype"),
            },
        },
    }
    second = {
        "error": [],
        "result": {
            "count": 3,
            "ledger": {
                "L2": entry(timestamp="1767225600", entry_type="future_type"),
                "L3": entry(timestamp="1767225602"),
            },
        },
    }
    result = preview(client_with_pages(first, second), limit=2)
    assert result.fetched_pages == 2
    assert result.received_total == 4
    assert result.unique_total == 3
    assert result.duplicate_ids == ("L2",)
    assert not result.conflicting_duplicate_ids
    assert result.ready_for_import
    assert [item.ledger_id for item in result.diagnostics] == ["L1", "L2"]
    assert result.unknown_types == ("future_type",)
    assert result.unknown_subtypes == ("future_subtype",)
    assert result.counts_by_asset == {"XXBT": 3}
    reversed_result = preview(client_with_pages(first, second), limit=0)
    assert result.stable_ledger_id_digest == reversed_result.stable_ledger_id_digest


def test_incomplete_conflicting_and_malformed_pages_are_diagnostic() -> None:
    original = entry()
    changed = entry(amount="2")
    conflict = preview(
        client_with_pages(
            {"error": [], "result": {"count": 2, "ledger": {"L1": original}}},
            {"error": [], "result": {"count": 2, "ledger": {"L1": changed}}},
        )
    )
    assert conflict.conflicting_duplicate_ids == ("L1",)
    assert not conflict.ready_for_import

    stopped = preview(
        client_with_pages(
            {"error": [], "result": {"count": 2, "ledger": {"L1": original}}},
            {"error": [], "result": {"count": 2, "ledger": {}}},
        )
    )
    assert not stopped.pagination_complete
    assert "keinen Fortschritt" in stopped.warnings[0]
    assert any("Gesamtzahl" in warning for warning in stopped.warnings)

    malformed_values = [
        "not-an-object",
        entry(timestamp="invalid"),
        entry(timestamp="NaN"),
        entry(amount="NaN"),
        entry(fee="NaN"),
        entry(entry_type=1),
        entry(asset=1),
        entry(subtype=1),
        entry() | {"refid": 1},
        entry() | {"balance": "NaN"},
        {},
    ]
    for raw in malformed_values:
        malformed = preview(
            client_with_pages(
                {"error": [], "result": {"count": 1, "ledger": {"L1": raw}}}
            )
        )
        assert malformed.malformed_entries == 1
        assert not malformed.ready_for_import


def test_preview_input_validation_and_empty_account() -> None:
    provider = client_with_pages({"error": [], "result": {"count": 0, "ledger": {}}})
    assert (
        preview(provider).stable_ledger_id_digest
        == __import__("hashlib").sha256(b"").hexdigest()
    )
    for kwargs in [
        {"start": datetime(2026, 1, 1), "end": None, "diagnostic_limit": 0},
        {"start": None, "end": datetime(2026, 1, 2), "diagnostic_limit": 0},
        {"start": START, "end": START, "diagnostic_limit": 0},
        {"start": None, "end": None, "diagnostic_limit": 101},
    ]:
        with pytest.raises(ValueError):
            client_with_pages().ledger_preview(asset=None, ledger_type="all", **kwargs)

    limited = KrakenPrivateClient(
        api_key="synthetic-key",
        api_secret=SECRET,
        max_pages=1,
        opener=lambda _request, _timeout: FakeResponse(
            {"error": [], "result": {"count": 2, "ledger": {"L1": entry()}}}
        ),
    )
    limited_result = preview(limited)
    assert not limited_result.pagination_complete
    assert "Sicherheitsgrenze" in limited_result.warnings[0]

    filtered = client_with_pages(
        {"error": [], "result": {"count": 0, "ledger": {}}}
    ).ledger_preview(
        start=None,
        end=None,
        asset="XXBT",
        ledger_type="",
        diagnostic_limit=0,
    )
    assert filtered.ready_for_import


class MockHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        assert self.path == "/0/private/Ledgers"
        assert self.headers["API-Key"] == "synthetic-key"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":[],"result":{"ledger":{},"count":0}}')

    def log_message(self, _: str, *args: object) -> None:
        return None


def test_adapter_against_local_mock_server() -> None:
    server = HTTPServer(("127.0.0.1", 0), MockHandler)
    worker = threading.Thread(target=server.handle_request)
    worker.start()
    try:
        provider = KrakenPrivateClient(
            api_key="synthetic-key",
            api_secret=SECRET,
            base_url=f"http://127.0.0.1:{server.server_port}",
            max_retries=0,
        )
        assert preview(provider).ready_for_import
    finally:
        worker.join(timeout=2)
        server.server_close()


def configured_settings() -> Settings:
    return Settings(
        kraken_api_key="synthetic-key",
        kraken_api_secret=SECRET,
        kraken_api_base_url="https://api.kraken.com",
    )


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)

    def database_dependency() -> Iterator[object]:
        with sessions() as session:
            yield session

    app.dependency_overrides[get_settings] = configured_settings
    app.dependency_overrides[get_session] = database_dependency
    get_settings.cache_clear()
    with TestClient(app) as value:
        yield value
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_connection_and_preview_api_contracts(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    complete = preview(
        client_with_pages(
            {"error": [], "result": {"count": 1, "ledger": {"L1": entry()}}}
        )
    )

    class StubClient:
        def check_ledger_access(self) -> None:
            return None

        def ledger_preview(self, **_: object) -> LedgerPreview:
            return complete

    monkeypatch.setattr(kraken_live, "build_kraken_client", lambda _: StubClient())
    connection_response = api_client.get("/api/kraken/connection")
    response = api_client.post(
        "/api/kraken/ledger-preview",
        json={
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-01-02T00:00:00Z",
            "diagnostic_limit": 1,
        },
    )
    assert connection_response.json()["ledger_permission_available"] is True
    assert response.status_code == 200
    assert response.json()["diagnostics"][0] == {
        "ledger_id": "L1",
        "occurred_at": "2026-01-01T00:00:00.125000Z",
        "entry_type": "trade",
        "subtype": "",
        "asset": "XXBT",
    }
    body = response.text
    assert "synthetic-key" not in body
    assert SECRET not in body


def test_api_configuration_validation_errors_and_openapi(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings()
    assert api_client.get("/api/kraken/connection").json() == {
        "configured": False,
        "reachable": False,
        "authenticated": False,
        "ledger_permission_available": False,
        "message": "Kraken-Lesezugriff ist serverseitig nicht konfiguriert.",
    }
    invalid = api_client.post(
        "/api/kraken/ledger-preview",
        json={"start": "2026-01-02T00:00:00Z", "end": "2026-01-01T00:00:00Z"},
    )
    assert invalid.status_code == 400
    naive = api_client.post(
        "/api/kraken/ledger-preview", json={"start": "2026-01-01T00:00:00"}
    )
    assert naive.status_code == 400
    for body in (
        {"asset": "BTC/../../"},
        {"asset": "BÜC"},
        {"ledger_type": "unknown"},
    ):
        filtered_error = api_client.post("/api/kraken/ledger-preview", json=body)
        assert filtered_error.status_code == 400
        assert filtered_error.json()["detail"]["code"] == "kraken_invalid_filter"

    built = kraken_live.build_kraken_client(configured_settings())
    assert built.base_url == "https://api.kraken.com"
    with pytest.raises(KrakenPrivateError, match="nur in Tests"):
        kraken_live.build_kraken_client(
            Settings(
                ENV="production",
                kraken_api_key="key",
                kraken_api_secret=SECRET,
                kraken_api_base_url="https://example.invalid",
            )
        )

    class FailingClient:
        def __init__(self, code: str) -> None:
            self.code = code

        def ledger_preview(self, **_: object) -> LedgerPreview:
            raise KrakenPrivateError(self.code, "Sichere Meldung")

    for code, status in [
        ("kraken_authentication_failed", 401),
        ("kraken_ledger_permission_missing", 403),
        ("kraken_rate_limited", 429),
        ("kraken_invalid_response", 502),
        ("kraken_unavailable", 503),
        ("kraken_timeout", 504),
        ("unexpected_code", 502),
    ]:
        monkeypatch.setattr(
            kraken_live, "build_kraken_client", lambda _, code=code: FailingClient(code)
        )
        response = api_client.post("/api/kraken/ledger-preview", json={})
        assert response.status_code == status
        assert response.json()["detail"]["code"] == code

    schema = api_client.get("/openapi.json").json()
    serialized = json.dumps(schema).lower()
    assert "kraken_api_secret" not in serialized
    assert "kraken_api_key" not in serialized


def test_connection_classification_and_preview_conflict(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailingClient:
        def __init__(self, code: str) -> None:
            self.code = code

        def check_ledger_access(self) -> None:
            raise KrakenPrivateError(self.code, "Sichere Meldung")

    for code, expected in [
        ("kraken_ledger_permission_missing", (True, True, False)),
        ("kraken_authentication_failed", (True, False, False)),
        ("kraken_unavailable", (False, False, False)),
    ]:
        monkeypatch.setattr(
            kraken_live, "build_kraken_client", lambda _, code=code: FailingClient(code)
        )
        body = api_client.get("/api/kraken/connection").json()
        assert (
            body["reachable"],
            body["authenticated"],
            body["ledger_permission_available"],
        ) == expected

    incomplete = preview(
        client_with_pages(
            {"error": [], "result": {"count": 2, "ledger": {"L1": entry()}}},
            {"error": [], "result": {"count": 2, "ledger": {}}},
        )
    )
    monkeypatch.setattr(
        kraken_live,
        "build_kraken_client",
        lambda _: type(
            "Stub", (), {"ledger_preview": lambda self, **kwargs: incomplete}
        )(),
    )
    response = api_client.post("/api/kraken/ledger-preview", json={})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "kraken_ledger_preview_incomplete"


def test_preview_routes_do_not_reference_persistence() -> None:
    source = inspect.getsource(kraken_live.ledger_preview)
    for forbidden in ("ImportSession", "RawImportRecord", "Session", "commit(", "add("):
        assert forbidden not in source


def full_csv(*rows: str, bom: bool = False) -> bytes:
    header = (
        "txid,refid,time,type,subtype,aclass,subclass,asset,wallet,amount,fee,balance\n"
    )
    return (("\ufeff" if bom else "") + header + "\n".join(rows) + "\n").encode()


def test_local_half_open_period_preserves_microseconds_across_pages() -> None:
    timestamps = {
        "BEFORE": "1767225599.999999",
        "START": "1767225600.000000",
        "INSIDE": "1767225600.000001",
        "LAST": "1767311999.999999",
        "END": "1767312000.000000",
        "AFTER": "1767312000.000001",
    }
    first = {
        "error": [],
        "result": {
            "count": 6,
            "ledger": {
                key: entry(timestamp=value)
                for key, value in list(timestamps.items())[:3]
            },
        },
    }
    second = {
        "error": [],
        "result": {
            "count": 6,
            "ledger": {
                key: entry(timestamp=value)
                for key, value in list(timestamps.items())[3:]
            },
        },
    }
    result = preview(client_with_pages(first, second), limit=10)
    assert [item.ledger_id for item in result.records] == ["START", "INSIDE", "LAST"]
    assert result.records[1].occurred_at.microsecond == 1
    assert result.stable_ledger_id_digest == ledger_digest(
        tuple(canonical_from_api(item) for item in result.records)
    )


@pytest.mark.parametrize(
    ("raw", "normalized", "marker", "variant", "kind"),
    [
        ("XXBT", "BTC", None, None, KrakenAssetNormalizationKind.ALIAS),
        ("XETH", "ETH", None, None, KrakenAssetNormalizationKind.ALIAS),
        ("ADA", "ADA", None, None, KrakenAssetNormalizationKind.IDENTITY),
        ("EIGEN", "EIGEN", None, None, KrakenAssetNormalizationKind.IDENTITY),
        ("XTZ", "XTZ", None, None, KrakenAssetNormalizationKind.IDENTITY),
        ("ADA.S", "ADA", None, "S", KrakenAssetNormalizationKind.PRODUCT_VARIANT),
        ("XTZ.S", "XTZ", None, "S", KrakenAssetNormalizationKind.PRODUCT_VARIANT),
        ("XTZ.B", "XTZ", None, "B", KrakenAssetNormalizationKind.PRODUCT_VARIANT),
        ("KAVA21.S", "KAVA", "21", "S", KrakenAssetNormalizationKind.PRODUCT_VARIANT),
        ("GRT28.S", "GRT", "28", "S", KrakenAssetNormalizationKind.PRODUCT_VARIANT),
        ("DOT28.S", "DOT", "28", "S", KrakenAssetNormalizationKind.PRODUCT_VARIANT),
        ("ATOM21.S", "ATOM", "21", "S", KrakenAssetNormalizationKind.PRODUCT_VARIANT),
        ("XXBT.B", "BTC", None, "B", KrakenAssetNormalizationKind.PRODUCT_VARIANT),
        ("XETH.B", "ETH", None, "B", KrakenAssetNormalizationKind.PRODUCT_VARIANT),
        ("NEWCOIN9", "NEWCOIN9", None, None, KrakenAssetNormalizationKind.IDENTITY),
    ],
)
def test_asset_normalization_keeps_raw_and_product_variant(
    raw: str,
    normalized: str,
    marker: str | None,
    variant: str | None,
    kind: KrakenAssetNormalizationKind,
) -> None:
    identity = normalize_asset(raw)
    actual = (
        identity.raw_asset,
        identity.normalized_asset,
        identity.product_marker,
        identity.product_variant,
        identity.is_unambiguous,
        identity.alias_kind,
    )
    assert actual == (
        raw,
        normalized,
        marker,
        variant,
        True,
        kind,
    )


@pytest.mark.parametrize("raw", ["", "ADA/S", "ADA..S", "ADA.X", "ada", " ADA"])
def test_asset_normalization_rejects_only_invalid_or_ambiguous_codes(raw: str) -> None:
    identity = normalize_asset(raw)
    assert identity.raw_asset == raw
    assert identity.normalized_asset is None
    assert identity.is_unambiguous is False
    assert identity.alias_kind is KrakenAssetNormalizationKind.INVALID
    assert identity.product_marker is None
    assert identity.product_variant is None


def test_canonical_record_keeps_product_marker_without_inventing_wallet() -> None:
    record = canonical_from_api(
        kraken_private.LedgerEntry(
            ledger_id="PRODUCT",
            occurred_at=START,
            entry_type="staking",
            subtype="",
            asset="KAVA21.S",
            amount=Decimal("1"),
            fee=Decimal("0"),
            extra={"refid": "R-PRODUCT"},
        )
    )
    assert record.asset_raw == "KAVA21.S"
    assert record.asset_normalized == "KAVA"
    assert (
        record.asset_normalization_kind is KrakenAssetNormalizationKind.PRODUCT_VARIANT
    )
    assert record.product_marker == "21"
    assert record.product_variant == "S"
    assert record.wallet_label is None


def test_real_csv_contract_bom_duplicates_and_validation() -> None:
    row = "L1,R1,2026-01-01 00:00:00,earn,reward,currency,,XXBT,spot,1.25,0.01,4.00"
    parsed = parse_ledger_csv(full_csv(row, row, bom=True))
    assert parsed.duplicate_ids == ("L1",)
    assert not parsed.conflicting_duplicate_ids
    record = parsed.records[0]
    assert record.asset_raw == "XXBT"
    assert record.asset_normalized == "BTC"
    assert record.wallet_label == "spot"
    assert record.amount == Decimal("1.25")
    assert record.occurred_at.tzinfo is UTC

    conflict = parse_ledger_csv(full_csv(row, row.replace("1.25", "2.25")))
    assert conflict.conflicting_duplicate_ids == ("L1",)
    invalid = parse_ledger_csv(full_csv(row.replace("1.25", "not-decimal")))
    assert invalid.malformed_entries == 1
    with pytest.raises(ValueError, match="Fehlende Ledger-Spalten"):
        parse_ledger_csv(b"txid,time\nL1,2026-01-01\n")
    with pytest.raises(ValueError, match="leer"):
        parse_ledger_csv(b"")


def test_csv_contract_rejects_encoding_structure_and_missing_values() -> None:
    with pytest.raises(ValueError, match="UTF-8"):
        parse_ledger_csv(b"\xff")
    header_only = full_csv().decode()
    with pytest.raises(ValueError, match="keine Datensätze"):
        parse_ledger_csv(header_only)

    too_many_fields = header_only + (
        "L1,R1,2026-01-01 00:00:00,trade,,currency,,XXBT,spot,1,0,1,extra\n"
    )
    malformed_shape = parse_ledger_csv(too_many_fields)
    assert malformed_shape.malformed_entries == 1
    assert malformed_shape.warnings == (
        "Die CSV enthält fehlerhafte Pflichtdatensätze.",
    )

    short_row = header_only + "L1,R1,2026-01-01 00:00:00,trade\n"
    assert parse_ledger_csv(short_row).malformed_entries == 1
    empty_id = header_only + (
        ",R1,2026-01-01 00:00:00,trade,,currency,,XXBT,spot,1,0,1\n"
    )
    assert parse_ledger_csv(empty_id).malformed_entries == 1
    without_balance = parse_ledger_csv(
        full_csv("L2,R2,2026-01-01 00:00:00,trade,,currency,,XXBT,spot,1,0,")
    )
    assert without_balance.records[0].balance is None


def test_canonical_event_contract_distinguishes_exact_unknown_and_empty_types() -> None:
    csv_record = parse_ledger_csv(
        full_csv("L1,R1,2026-01-01 00:00:00,trade,,currency,,XXBT,spot,1,0,1")
    ).records[0]
    assert csv_record.normalized_event == "trade"
    assert replace(csv_record, csv_type="").normalized_event is None

    api_record = canonical_from_api(
        kraken_private.LedgerEntry(
            ledger_id="L1",
            occurred_at=START,
            entry_type="trade",
            subtype="",
            asset="XXBT",
            amount=Decimal("1"),
            fee=Decimal("0"),
            extra={"refid": "R1", "balance": "1"},
        )
    )
    comparison = compare_ledgers(
        ParsedLedgerBatch(
            records=(csv_record,),
            duplicate_ids=(),
            conflicting_duplicate_ids=(),
            malformed_entries=0,
            warnings=(),
        ),
        (api_record,),
        diagnostic_limit=0,
    )
    assert comparison.exact_match_count == 1
    assert comparison.normalized_match_count == 0
    assert comparison.ready_for_import


def test_csv_api_comparison_normalizes_time_type_and_noncomparable_wallet() -> None:
    csv_batch = parse_ledger_csv(
        full_csv(
            "L1,R1,2026-01-01 00:00:00,earn,reward,currency,,XXBT,spot,1.25,0.01,4.00"
        )
    )
    api_entry = kraken_private.LedgerEntry(
        ledger_id="L1",
        occurred_at=datetime(2026, 1, 1, 0, 0, 0, 999999, tzinfo=UTC),
        entry_type="staking",
        subtype="",
        asset="XXBT",
        amount=Decimal("1.25"),
        fee=Decimal("0.01"),
        extra={"refid": "R1", "balance": "4.00", "aclass": "currency"},
    )
    result = compare_ledgers(
        csv_batch, (canonical_from_api(api_entry),), diagnostic_limit=10
    )
    assert result.ready_for_import
    assert result.normalized_match_count == 1
    assert result.timestamp_precision_only_count == 1
    assert result.not_comparable_fields == ("wallet",)
    assert result.csv_digest == result.api_digest
    assert filter_records(csv_batch.records, START, datetime(2026, 1, 2, tzinfo=UTC))


def test_real_identity_and_product_assets_are_import_ready() -> None:
    pairs = (
        ("L1", "ADA", "ADA.S"),
        ("L2", "ADA", "ADA"),
        ("L3", "XTZ", "XTZ.S"),
        ("L4", "XTZ", "XTZ.B"),
        ("L5", "XTZ", "XTZ"),
        ("L6", "EIGEN", "EIGEN"),
    )
    csv_batch = parse_ledger_csv(
        full_csv(
            *(
                f"{ledger_id},R-{ledger_id},2026-01-01 00:00:00,earn,reward,"
                f"currency,,{csv_asset},spot,1,0,10"
                for ledger_id, csv_asset, _ in pairs
            )
        )
    )
    api_records = tuple(
        canonical_from_api(
            kraken_private.LedgerEntry(
                ledger_id=ledger_id,
                occurred_at=datetime(2026, 1, 1, 0, 0, 0, 123456, tzinfo=UTC),
                entry_type="staking",
                subtype="",
                asset=api_asset,
                amount=Decimal("1"),
                fee=Decimal("0"),
                extra={"refid": f"R-{ledger_id}", "balance": "10"},
            )
        )
        for ledger_id, _, api_asset in pairs
    )
    before_digest = ledger_digest(csv_batch.records)
    result = compare_ledgers(csv_batch, api_records, diagnostic_limit=10)
    assert result.unknown_asset_mappings == ()
    assert result.field_mismatch_count == 0
    assert result.normalized_match_count == 6
    assert result.timestamp_precision_only_count == 6
    assert result.not_comparable_fields == ("wallet",)
    assert result.csv_digest == result.api_digest == before_digest
    assert result.ready_for_import
    assert [
        (record.ledger_id, record.amount, record.fee) for record in api_records
    ] == [(ledger_id, Decimal("1"), Decimal("0")) for ledger_id, _, _ in pairs]
    assert [record.asset_raw for record in api_records] == [
        "ADA.S",
        "ADA",
        "XTZ.S",
        "XTZ.B",
        "XTZ",
        "EIGEN",
    ]
    assert (
        api_records[-1].asset_normalization_kind
        is KrakenAssetNormalizationKind.IDENTITY
    )
    assert all(record.wallet_label is None for record in api_records)


def test_comparison_blocks_missing_ids_unknown_mappings_and_csv_conflicts() -> None:
    row = "L1,R1,2026-01-01 00:00:00,future,event,currency,,FUTURE/7,spot,1,0,1"
    csv_batch = parse_ledger_csv(full_csv(row, row.replace(",1,0,1", ",2,0,1")))
    api = canonical_from_api(
        kraken_private.LedgerEntry(
            ledger_id="L2",
            occurred_at=START,
            entry_type="future",
            subtype="",
            asset="FUTURE/7",
            amount=Decimal("1"),
            fee=Decimal("0"),
            extra={"refid": "R2"},
        )
    )
    result = compare_ledgers(csv_batch, (api,), diagnostic_limit=0)
    assert not result.ready_for_import
    assert result.missing_in_api == ("L1",)
    assert result.missing_in_csv == ("L2",)
    assert result.unknown_asset_mappings == ("FUTURE/7",)
    assert csv_batch.conflicting_duplicate_ids == ("L1",)
    common_unknown = replace(api, ledger_id="L1", refid="R1")
    mapped = compare_ledgers(csv_batch, (common_unknown,), diagnostic_limit=0)
    assert mapped.unknown_type_mappings == ("L1",)
    assert not mapped.ready_for_import


@pytest.mark.parametrize(
    ("changed", "field"),
    [
        ({"refid": "R2"}, "refid"),
        ({"asset": "XETH"}, "asset_normalized"),
        ({"amount": Decimal("2")}, "amount"),
        ({"fee": Decimal("2")}, "fee"),
        ({"balance": "5"}, "balance"),
        ({"occurred_at": datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)}, "occurred_at"),
        ({"entry_type": "trade"}, "event_type"),
    ],
)
def test_comparison_reports_material_field_mismatches(
    changed: dict[str, object], field: str
) -> None:
    csv_batch = parse_ledger_csv(
        full_csv(
            "L1,R1,2026-01-01 00:00:00,earn,reward,currency,,XXBT,spot,1.25,0.01,4.00"
        )
    )
    extra: dict[str, object] = {"refid": "R1", "balance": "4.00"}
    if "refid" in changed:
        extra["refid"] = changed["refid"]
    if "balance" in changed:
        extra["balance"] = changed["balance"]
    occurred = changed.get("occurred_at", START)
    entry_type = changed.get("entry_type", "staking")
    asset = changed.get("asset", "XXBT")
    amount = changed.get("amount", Decimal("1.25"))
    fee = changed.get("fee", Decimal("0.01"))
    assert isinstance(occurred, datetime)
    assert isinstance(entry_type, str)
    assert isinstance(asset, str)
    assert isinstance(amount, Decimal)
    assert isinstance(fee, Decimal)
    api = canonical_from_api(
        kraken_private.LedgerEntry(
            ledger_id="L1",
            occurred_at=occurred,
            entry_type=entry_type,
            subtype="",
            asset=asset,
            amount=amount,
            fee=fee,
            extra=extra,
        )
    )
    result = compare_ledgers(csv_batch, (api,), diagnostic_limit=1)
    assert not result.ready_for_import
    assert result.mismatches_by_field[field] == 1
    assert result.diagnostic_ids == ("L1",)


def test_compare_and_confirmed_import_api_are_nonpersistent_then_idempotent(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    complete = preview(
        client_with_pages(
            {
                "error": [],
                "result": {
                    "count": 1,
                    "ledger": {
                        "L1": entry(
                            timestamp="1767225600.125",
                            entry_type="staking",
                            subtype="",
                        )
                        | {"balance": "4", "aclass": "currency"}
                    },
                },
            }
        )
    )

    class StubClient:
        def ledger_preview(self, **_: object) -> LedgerPreview:
            return complete

    monkeypatch.setattr(kraken_live, "build_kraken_client", lambda _: StubClient())
    content = full_csv(
        "L1,synthetic-reference,2026-01-01 00:00:00,earn,reward,currency,,"
        "XXBT,spot,1.25,0.01,4"
    )
    compared = api_client.post(
        "/api/kraken/ledger-compare",
        data={
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-01-02T00:00:00Z",
            "diagnostic_limit": "10",
        },
        files={"file": ("ledger.csv", content, "text/csv")},
    )
    assert compared.status_code == 200
    assert compared.json()["ready_for_import"] is True
    assert api_client.get("/api/dashboard").json()["imports"] == 0
    assert api_client.get("/api/dashboard").json()["raw_records"] == 0

    payload = {
        "start": "2026-01-01T00:00:00Z",
        "end": "2026-01-02T00:00:00Z",
        "expected_ledger_id_digest": compared.json()["api_ledger_id_digest"],
        "explicit_confirmation": True,
    }
    first = api_client.post("/api/kraken/ledger-import", json=payload)
    second = api_client.post("/api/kraken/ledger-import", json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json()["created_records"] == 1
    assert first.json()["transformed"] is False
    assert second.json()["created_records"] == 0
    assert second.json()["reused_records"] == 1
    assert api_client.get("/api/dashboard").json()["raw_records"] == 1
    transformed = api_client.post(
        "/api/kraken/ledger-import", json=payload | {"transform": True}
    )
    assert transformed.status_code == 200
    assert transformed.json()["transformed"] is True
    summary = transformed.json()["transformation_summary"]
    assert summary["checked"] == 1
    assert summary["contract_version"] == "kraken-domain-v2"
    assert summary["created_objects"] == 2
    assert summary["reused_objects"] == 0
    dashboard = api_client.get("/api/dashboard").json()
    assert dashboard["rewards"] == 1
    assert dashboard["open_valuations"] == 1
    detail = api_client.get(f"/api/imports/{first.json()['import_session_id']}").text
    assert "synthetic-key" not in detail
    assert SECRET not in detail


def test_confirmed_import_rejects_confirmation_digest_and_incomplete_preview(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    complete = preview(
        client_with_pages(
            {"error": [], "result": {"count": 1, "ledger": {"L1": entry()}}}
        )
    )
    monkeypatch.setattr(
        kraken_live,
        "build_kraken_client",
        lambda _: type(
            "Stub", (), {"ledger_preview": lambda self, **kwargs: complete}
        )(),
    )
    base = {
        "start": "2026-01-01T00:00:00Z",
        "end": "2026-01-02T00:00:00Z",
        "expected_ledger_id_digest": "0" * 64,
        "explicit_confirmation": False,
    }
    denied = api_client.post("/api/kraken/ledger-import", json=base)
    assert denied.status_code == 400
    changed = api_client.post(
        "/api/kraken/ledger-import", json=base | {"explicit_confirmation": True}
    )
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "kraken_ledger_changed"
    assert changed.json()["detail"]["actual_digest"] == complete.stable_ledger_id_digest

    incomplete = replace(
        complete,
        ready_for_import=False,
        pagination_complete=False,
    )
    monkeypatch.setattr(
        kraken_live,
        "build_kraken_client",
        lambda _: type(
            "Stub", (), {"ledger_preview": lambda self, **kwargs: incomplete}
        )(),
    )
    rejected = api_client.post(
        "/api/kraken/ledger-import",
        json=base
        | {
            "explicit_confirmation": True,
            "expected_ledger_id_digest": complete.stable_ledger_id_digest,
        },
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "kraken_ledger_incomplete"


def test_compare_api_validates_file_size_csv_and_provider_errors(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    period = {
        "start": "2026-01-01T00:00:00Z",
        "end": "2026-01-02T00:00:00Z",
    }
    wrong_file = api_client.post(
        "/api/kraken/ledger-compare",
        data=period,
        files={"file": ("ledger.txt", b"text")},
    )
    assert wrong_file.status_code == 400
    assert wrong_file.json()["detail"]["code"] == "kraken_csv_file_required"

    invalid = api_client.post(
        "/api/kraken/ledger-compare",
        data=period,
        files={"file": ("ledger.csv", b"invalid")},
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "kraken_csv_invalid"

    app.dependency_overrides[get_settings] = lambda: Settings(
        kraken_api_key="synthetic-key",
        kraken_api_secret=SECRET,
        max_upload_bytes=3,
    )
    too_large = api_client.post(
        "/api/kraken/ledger-compare",
        data=period,
        files={"file": ("ledger.csv", b"1234")},
    )
    assert too_large.status_code == 413
    app.dependency_overrides[get_settings] = configured_settings

    class FailingClient:
        def ledger_preview(self, **_: object) -> LedgerPreview:
            raise KrakenPrivateError("kraken_timeout", "Sichere Meldung")

    monkeypatch.setattr(kraken_live, "build_kraken_client", lambda _: FailingClient())
    provider_error = api_client.post(
        "/api/kraken/ledger-compare",
        data=period,
        files={
            "file": (
                "ledger.csv",
                full_csv("L1,R1,2026-01-01 00:00:00,trade,,currency,,XXBT,spot,1,0,1"),
            )
        },
    )
    assert provider_error.status_code == 504


def test_import_provider_failure_invalid_filters_and_canonical_conflict(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = {
        "start": "2026-01-01T00:00:00Z",
        "end": "2026-01-02T00:00:00Z",
        "expected_ledger_id_digest": "0" * 64,
        "explicit_confirmation": True,
    }
    invalid = api_client.post(
        "/api/kraken/ledger-import", json=base | {"ledger_type": "invalid"}
    )
    assert invalid.status_code == 400

    class FailingClient:
        def ledger_preview(self, **_: object) -> LedgerPreview:
            raise KrakenPrivateError("kraken_unavailable", "Sichere Meldung")

    monkeypatch.setattr(kraken_live, "build_kraken_client", lambda _: FailingClient())
    unavailable = api_client.post("/api/kraken/ledger-import", json=base)
    assert unavailable.status_code == 503

    first_preview = preview(
        client_with_pages(
            {"error": [], "result": {"count": 1, "ledger": {"L1": entry()}}}
        )
    )
    changed_preview = preview(
        client_with_pages(
            {
                "error": [],
                "result": {"count": 1, "ledger": {"L1": entry(amount="2")}},
            }
        )
    )
    current = first_preview

    class CurrentClient:
        def ledger_preview(self, **_: object) -> LedgerPreview:
            return current

    monkeypatch.setattr(kraken_live, "build_kraken_client", lambda _: CurrentClient())
    imported = api_client.post(
        "/api/kraken/ledger-import",
        json=base
        | {"expected_ledger_id_digest": first_preview.stable_ledger_id_digest},
    )
    assert imported.status_code == 200
    current = changed_preview
    conflict = api_client.post(
        "/api/kraken/ledger-import",
        json=base
        | {"expected_ledger_id_digest": changed_preview.stable_ledger_id_digest},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "canonical_record_conflict"
    assert api_client.get("/api/dashboard").json()["raw_records"] == 1
