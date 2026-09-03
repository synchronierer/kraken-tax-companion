from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.api import kraken_sync as sync_api
from app.config.settings import Settings, get_settings
from app.core.entities import RawImportRecord
from app.core.incremental_sync import (
    IncrementalSyncRun,
    SyncStatus,
    complete_sync,
    fail_sync,
)
from app.core.tax import ExportRun, TaxCalculationRun, TaxReviewDecision
from app.core.transformation import AcquisitionLot
from app.core.valuation import ValuationRun
from app.database.base import Base
from app.database.session import get_session
from app.imports.context import ImportContext
from app.imports.service import ImportOutcome, ImportResult
from app.infrastructure.kraken_private import (
    KrakenPrivateError,
    LedgerEntry,
    LedgerPreview,
)
from app.main import app
from app.services.kraken_sync import KrakenSyncError, KrakenSyncService

NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)


def database() -> Session:
    models.configure_mappings()
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)()


def preview(
    *ids: str,
    ready: bool = True,
    malformed: int = 0,
    complete: bool = True,
    conflicts: tuple[str, ...] = (),
) -> LedgerPreview:
    records = tuple(
        LedgerEntry(
            ledger_id=value,
            occurred_at=NOW - timedelta(days=1),
            entry_type="staking",
            subtype="",
            asset="XXBT",
            amount=Decimal("1"),
            fee=Decimal("0"),
            extra={"refid": value},
        )
        for value in ids
    )
    import hashlib

    digest = hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()
    return LedgerPreview(
        NOW - timedelta(days=8),
        NOW,
        2 if len(ids) > 1 else 1,
        len(ids),
        len(ids),
        len(ids),
        (),
        conflicts,
        records[0].occurred_at if records else None,
        records[-1].occurred_at if records else None,
        {"staking": len(ids)},
        {"": len(ids)},
        {"XXBT": len(ids)},
        (),
        (),
        malformed,
        complete,
        digest,
        (),
        ready,
        (),
        records,
    )


class Client:
    def __init__(self, result: LedgerPreview | Exception) -> None:
        self.result = result
        self.calls = 0

    def ledger_preview(self, **_: object) -> LedgerPreview:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def service(
    db: Session, client: Client, *, now: datetime = NOW, stale: int = 3600
) -> KrakenSyncService:
    return KrakenSyncService(
        db=db,
        client_factory=lambda: client,
        initial_start=datetime(2026, 1, 1, tzinfo=UTC),
        lookback_seconds=604800,
        settlement_lag_seconds=0,
        stale_seconds=stale,
        clock=lambda: now,
    )


def test_first_repeat_and_incremental_sync_are_idempotent() -> None:
    db = database()
    first = service(db, Client(preview("L1"))).run_sync()
    assert first.status is SyncStatus.COMPLETED
    assert first.new_raw_records == first.new_domain_objects // 2 == 1
    repeated = service(
        db, Client(preview("L1")), now=NOW + timedelta(hours=1)
    ).run_sync()
    assert repeated.known_records == 1 and repeated.new_raw_records == 0
    added = service(
        db, Client(preview("L1", "L2")), now=NOW + timedelta(hours=2)
    ).run_sync()
    assert added.known_records == added.new_raw_records == 1
    assert added.requested_from == repeated.requested_to - timedelta(days=7)
    assert db.scalar(select(func.count()).select_from(RawImportRecord)) == 2
    assert db.scalar(select(func.count()).select_from(AcquisitionLot)) == 2
    assert all(
        db.scalar(select(func.count()).select_from(model)) == 0
        for model in (ValuationRun, TaxCalculationRun, TaxReviewDecision, ExportRun)
    )
    assert (
        service(db, Client(preview()), now=NOW + timedelta(hours=3))
        .run_sync()
        .new_raw_records
        == 0
    )
    db.close()


@pytest.mark.parametrize(
    ("result", "code"),
    [
        (preview("L", complete=False), "kraken_sync_incomplete_pagination"),
        (preview("L", malformed=1), "kraken_sync_malformed_records"),
        (preview("L", conflicts=("L",)), "kraken_sync_conflicting_ledger_id"),
        (preview("L", ready=False), "kraken_sync_provider_count_mismatch"),
        (KrakenPrivateError("kraken_rate_limited", "Sicher"), "kraken_rate_limited"),
        (KrakenPrivateError("kraken_timeout", "Sicher"), "kraken_timeout"),
        (
            KrakenPrivateError("kraken_invalid_response", "Sicher"),
            "kraken_invalid_response",
        ),
    ],
)
def test_provider_failures_never_advance_checkpoint(
    result: LedgerPreview | Exception, code: str
) -> None:
    db = database()
    with pytest.raises(KrakenSyncError) as raised:
        service(db, Client(result)).run_sync()
    assert raised.value.code == code
    run = db.scalar(select(IncrementalSyncRun))
    assert run is not None and run.status is SyncStatus.FAILED
    assert service(db, Client(preview())).latest_successful_sync() is None
    db.close()


def test_conflict_parallel_guard_stale_recovery_and_terminal_immutability() -> None:
    db = database()
    active = IncrementalSyncRun(
        account_scope="default",
        sync_kind="spot_ledger",
        status=SyncStatus.PROCESSING,
        requested_from=NOW - timedelta(days=2),
        requested_to=NOW - timedelta(days=1),
        lookback_seconds=60,
        started_at=NOW - timedelta(minutes=1),
    )
    db.add(active)
    db.commit()
    client = Client(preview())
    with pytest.raises(KrakenSyncError, match="läuft") as raised:
        service(db, client).run_sync()
    assert raised.value.code == "kraken_sync_already_running" and client.calls == 0
    recovered = service(db, client, now=NOW + timedelta(hours=2), stale=60).run_sync()
    assert (
        db.get(IncrementalSyncRun, active.id).error_code
        == "kraken_sync_stale_recovered"
    )
    assert recovered.status is SyncStatus.COMPLETED
    recovered.error_count = 2
    with pytest.raises(ValueError, match="immutable"):
        db.commit()
    db.rollback()
    db.close()


def test_sync_domain_guards_and_transitions() -> None:
    with pytest.raises(ValueError):
        IncrementalSyncRun(
            account_scope="",
            sync_kind="spot_ledger",
            status=SyncStatus.PROCESSING,
            requested_from=NOW,
            requested_to=NOW,
            lookback_seconds=0,
            started_at=NOW,
        )
    with pytest.raises(ValueError, match="after start"):
        IncrementalSyncRun(
            account_scope="default",
            sync_kind="spot_ledger",
            status=SyncStatus.PROCESSING,
            requested_from=NOW,
            requested_to=NOW,
            lookback_seconds=1,
            started_at=NOW,
        )
    with pytest.raises(ValueError, match="lookback"):
        IncrementalSyncRun(
            account_scope="default",
            sync_kind="spot_ledger",
            status=SyncStatus.PROCESSING,
            requested_from=NOW - timedelta(days=1),
            requested_to=NOW,
            lookback_seconds=0,
            started_at=NOW,
        )
    ended = IncrementalSyncRun(
        account_scope="default",
        sync_kind="spot_ledger",
        status=SyncStatus.FAILED,
        requested_from=NOW - timedelta(days=1),
        requested_to=NOW,
        lookback_seconds=1,
        started_at=NOW,
        ended_at=NOW + timedelta(minutes=1),
    )
    assert ended.ended_at == NOW + timedelta(minutes=1)
    run = IncrementalSyncRun(
        account_scope="default",
        sync_kind="spot_ledger",
        status=SyncStatus.PROCESSING,
        requested_from=NOW - timedelta(days=1),
        requested_to=NOW,
        lookback_seconds=1,
        started_at=NOW,
    )
    complete_sync(run, NOW)
    with pytest.raises(ValueError):
        complete_sync(run, NOW)
    processing = IncrementalSyncRun(
        account_scope="default",
        sync_kind="spot_ledger",
        status=SyncStatus.PROCESSING,
        requested_from=NOW - timedelta(days=1),
        requested_to=NOW,
        lookback_seconds=1,
        started_at=NOW,
    )
    with pytest.raises(ValueError, match="precede"):
        complete_sync(processing, NOW - timedelta(seconds=1))
    with pytest.raises(ValueError, match="evidence"):
        fail_sync(processing, NOW, "", "summary")
    failed = IncrementalSyncRun(
        account_scope="default",
        sync_kind="spot_ledger",
        status=SyncStatus.PROCESSING,
        requested_from=NOW - timedelta(days=1),
        requested_to=NOW,
        lookback_seconds=1,
        started_at=NOW,
    )
    fail_sync(failed, NOW, "code", "summary")
    assert failed.error_count == 1
    with pytest.raises(ValueError):
        fail_sync(failed, NOW, "code", "summary")


def test_database_race_is_reported_as_parallel_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = database()
    client = Client(preview("L1"))

    def conflicting_commit() -> None:
        raise IntegrityError("INSERT", {}, RuntimeError("concurrent insert"))

    monkeypatch.setattr(db, "commit", conflicting_commit)
    with pytest.raises(KrakenSyncError) as raised:
        service(db, client).run_sync()
    assert raised.value.code == "kraken_sync_already_running"
    assert raised.value.run_id is None
    assert client.calls == 0
    assert len(db.new) == 0
    db.close()


def test_sync_api_status_list_detail_errors_and_no_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = database()

    def session_dependency():  # type: ignore[no-untyped-def]
        yield db

    settings = Settings(
        kraken_api_key="sentinel-key",
        kraken_api_secret="sentinel-secret",
        kraken_sync_initial_start=datetime(2026, 1, 1, tzinfo=UTC),
        kraken_sync_settlement_lag_seconds=0,
    )
    app.dependency_overrides[get_session] = session_dependency
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(
        sync_api, "build_kraken_client", lambda _: Client(preview("L1"))
    )
    try:
        with TestClient(app) as client:
            status = client.get("/api/kraken-sync")
            assert status.status_code == 200
            assert status.json()["configured"] is True
            assert status.json()["last_successful_sync"] is None
            created = client.post("/api/kraken-sync")
            assert created.status_code == 200
            run_id = created.json()["sync_run_id"]
            listing = client.get("/api/kraken-sync-runs?offset=0&limit=1")
            assert listing.json()["total"] == 1
            assert client.get(f"/api/kraken-sync-runs/{run_id}").status_code == 200
            assert client.get("/api/kraken-sync-runs/not-a-uuid").status_code == 422
            assert (
                client.get(
                    "/api/kraken-sync-runs/00000000-0000-0000-0000-000000000000"
                ).status_code
                == 404
            )
            assert client.get("/api/kraken-sync-runs?limit=0").status_code == 422
            output = created.text + listing.text
            assert "sentinel-key" not in output and "sentinel-secret" not in output
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_sync_api_maps_provider_and_parallel_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = database()

    def session_dependency():  # type: ignore[no-untyped-def]
        yield db

    settings = Settings(
        kraken_api_key="synthetic",
        kraken_api_secret="synthetic",
        kraken_sync_initial_start=datetime(2026, 1, 1, tzinfo=UTC),
        kraken_sync_settlement_lag_seconds=0,
    )
    app.dependency_overrides[get_session] = session_dependency
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(
        sync_api,
        "build_kraken_client",
        lambda _: Client(KrakenPrivateError("kraken_unavailable", "Sicher")),
    )
    try:
        with TestClient(app) as client:
            failed = client.post("/api/kraken-sync")
            assert failed.status_code == 503
            assert failed.json()["detail"]["code"] == "kraken_unavailable"
            current = datetime.now(UTC)
            active = IncrementalSyncRun(
                account_scope="default",
                sync_kind="spot_ledger",
                status=SyncStatus.PROCESSING,
                requested_from=current - timedelta(minutes=2),
                requested_to=current - timedelta(minutes=1),
                lookback_seconds=60,
                started_at=current,
            )
            db.add(active)
            db.commit()
            provider = Client(preview())
            monkeypatch.setattr(sync_api, "build_kraken_client", lambda _: provider)
            concurrent = client.post("/api/kraken-sync")
            assert concurrent.status_code == 409
            assert concurrent.json()["detail"]["code"] == (
                "kraken_sync_already_running"
            )
            assert provider.calls == 0
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_provider_change_and_internal_failure_are_atomic() -> None:
    class ChangingClient(Client):
        def __init__(self) -> None:
            self.results = iter((preview("L1"), preview("L1", "L2")))
            self.calls = 0

        def ledger_preview(self, **_: object) -> LedgerPreview:
            self.calls += 1
            return next(self.results)

    db = database()
    with pytest.raises(KrakenSyncError) as changed:
        service(db, ChangingClient()).run_sync()
    assert changed.value.code == "kraken_sync_provider_changed"
    assert db.scalar(select(func.count()).select_from(RawImportRecord)) == 0

    class ExplodingFactory:
        def __call__(self) -> Client:
            raise RuntimeError("sentinel must not escape")

    broken = KrakenSyncService(
        db=db,
        client_factory=ExplodingFactory(),
        initial_start=datetime(2026, 1, 1, tzinfo=UTC),
        lookback_seconds=60,
        settlement_lag_seconds=0,
        stale_seconds=60,
        clock=lambda: NOW + timedelta(hours=1),
    )
    with pytest.raises(KrakenSyncError) as internal:
        broken.run_sync()
    assert internal.value.code == "kraken_sync_internal_failure"
    assert "sentinel" not in internal.value.message
    db.close()


def test_existing_ledger_identity_conflict_preserves_original() -> None:
    db = database()
    service(db, Client(preview("L1"))).run_sync()
    original = db.scalar(select(RawImportRecord))
    assert original is not None
    old_payload = dict(original.payload)
    base = preview("L1")
    altered = replace(
        base,
        records=(replace(base.records[0], amount=Decimal("2")),),
    )
    with pytest.raises(KrakenSyncError) as conflict:
        service(db, Client(altered), now=NOW + timedelta(hours=1)).run_sync()
    assert conflict.value.code == "canonical_record_conflict"
    db.refresh(original)
    assert original.payload == old_payload
    assert db.scalar(select(func.count()).select_from(AcquisitionLot)) == 1
    db.close()


def test_import_and_transformation_failures_roll_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = database()

    def failed_import(self: object, **kwargs: object) -> ImportResult:
        session = cast(ImportContext, kwargs["context"]).session
        return ImportResult(
            session_id=session.id,
            content_hash=None,
            skipped=False,
            outcome=ImportOutcome.FAILED,
        )

    from app.services import kraken_sync as service_module

    monkeypatch.setattr(service_module.ImportService, "import_records", failed_import)
    with pytest.raises(KrakenSyncError) as imported:
        service(db, Client(preview("L1"))).run_sync()
    assert imported.value.code == "kraken_sync_import_failed"
    assert db.scalar(select(func.count()).select_from(RawImportRecord)) == 0
    monkeypatch.undo()

    class FailedStatus:
        value = "failed"

    class FailedTransformation:
        status = FailedStatus()

    monkeypatch.setattr(
        service_module.KrakenTransformationService,
        "transform",
        lambda self, **kwargs: FailedTransformation(),
    )
    with pytest.raises(KrakenSyncError) as transformed:
        service(db, Client(preview("L2")), now=NOW + timedelta(hours=1)).run_sync()
    assert transformed.value.code == "kraken_sync_transformation_failed"
    assert db.scalar(select(func.count()).select_from(RawImportRecord)) == 0
    db.close()


def test_invalid_window_settings_and_counter_guards() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(kraken_sync_initial_start=datetime(2026, 1, 1))
    with pytest.raises(ValidationError):
        Settings(kraken_sync_lookback_seconds=0)
    db = database()
    future = service(db, Client(preview()), now=datetime(2025, 1, 1, tzinfo=UTC))
    with pytest.raises(KrakenSyncError) as invalid:
        future.run_sync()
    assert invalid.value.code == "kraken_sync_window_invalid"
    with pytest.raises(ValueError, match="counters"):
        IncrementalSyncRun(
            account_scope="default",
            sync_kind="spot_ledger",
            status=SyncStatus.PROCESSING,
            requested_from=NOW - timedelta(days=1),
            requested_to=NOW,
            lookback_seconds=1,
            started_at=NOW,
            error_count=-1,
        )
    db.close()
