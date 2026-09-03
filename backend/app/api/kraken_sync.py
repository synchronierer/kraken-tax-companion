from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.kraken_live import _STATUS_BY_CODE, build_kraken_client
from app.config.settings import Settings, get_settings
from app.core.incremental_sync import IncrementalSyncRun
from app.database.session import get_session
from app.services.kraken_sync import KrakenSyncError, KrakenSyncService

router = APIRouter(prefix="/api", tags=["Kraken-Sync"])
Db = Annotated[Session, Depends(get_session)]


class SyncRunResponse(BaseModel):
    sync_run_id: UUID
    status: str
    requested_start: datetime
    requested_end: datetime
    started_at: datetime
    ended_at: datetime | None
    lookback_seconds: int
    fetched_pages: int
    provider_records: int
    unique_records: int
    known_records: int
    new_raw_records: int
    new_domain_objects: int
    reviews: int
    errors: int
    ledger_id_digest: str | None
    error_code: str | None
    error_summary: str | None


class SyncStatusResponse(BaseModel):
    configured: bool
    last_successful_sync: SyncRunResponse | None
    processing_sync: SyncRunResponse | None
    requested_start: datetime
    requested_end: datetime
    lookback_seconds: int


class SyncRunsResponse(BaseModel):
    items: list[SyncRunResponse]
    total: int
    offset: int
    limit: int


def _service(db: Session, settings: Settings) -> KrakenSyncService:
    return KrakenSyncService(
        db=db,
        client_factory=lambda: build_kraken_client(settings),
        initial_start=settings.kraken_sync_initial_start,
        lookback_seconds=settings.kraken_sync_lookback_seconds,
        settlement_lag_seconds=settings.kraken_sync_settlement_lag_seconds,
        stale_seconds=settings.kraken_sync_stale_seconds,
    )


def _run(item: IncrementalSyncRun) -> SyncRunResponse:
    return SyncRunResponse(
        sync_run_id=item.id,
        status=item.status.value,
        requested_start=item.requested_from,
        requested_end=item.requested_to,
        started_at=item.started_at,
        ended_at=item.ended_at,
        lookback_seconds=item.lookback_seconds,
        fetched_pages=item.fetched_pages,
        provider_records=item.provider_records,
        unique_records=item.unique_records,
        known_records=item.known_records,
        new_raw_records=item.new_raw_records,
        new_domain_objects=item.new_domain_objects,
        reviews=item.review_count,
        errors=item.error_count,
        ledger_id_digest=item.ledger_id_digest,
        error_code=item.error_code,
        error_summary=item.error_summary,
    )


@router.get("/kraken-sync", response_model=SyncStatusResponse)
def sync_status(db: Db, settings: Annotated[Settings, Depends(get_settings)]) -> Any:
    plan = _service(db, settings).plan_sync()
    last_successful = (
        _run(plan.last_successful) if plan.last_successful is not None else None
    )
    processing = _run(plan.processing) if plan.processing is not None else None
    return {
        "configured": bool(settings.kraken_api_key and settings.kraken_api_secret),
        "last_successful_sync": last_successful,
        "processing_sync": processing,
        "requested_start": plan.requested_from,
        "requested_end": plan.requested_to,
        "lookback_seconds": plan.lookback_seconds,
    }


@router.post("/kraken-sync", response_model=SyncRunResponse)
def start_sync(
    db: Db, settings: Annotated[Settings, Depends(get_settings)]
) -> SyncRunResponse:
    try:
        return _run(_service(db, settings).run_sync())
    except KrakenSyncError as error:
        status = (
            409
            if error.code
            in {
                "kraken_sync_already_running",
                "canonical_record_conflict",
                "kraken_sync_conflicting_ledger_id",
                "kraken_sync_incomplete_pagination",
                "kraken_sync_provider_count_mismatch",
                "kraken_sync_malformed_records",
                "kraken_sync_provider_changed",
            }
            else _STATUS_BY_CODE.get(error.code, 500)
        )
        raise HTTPException(
            status,
            detail={
                "code": error.code,
                "message": error.message,
                "sync_run_id": str(error.run_id) if error.run_id else None,
            },
        ) from error


@router.get("/kraken-sync-runs", response_model=SyncRunsResponse)
def sync_runs(
    db: Db,
    settings: Annotated[Settings, Depends(get_settings)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Any:
    items = _service(db, settings).list_sync_runs()
    return {
        "items": [_run(item) for item in items[offset : offset + limit]],
        "total": len(items),
        "offset": offset,
        "limit": limit,
    }


@router.get("/kraken-sync-runs/{run_id}", response_model=SyncRunResponse)
def sync_run(run_id: UUID, db: Db) -> SyncRunResponse:
    item = db.get(IncrementalSyncRun, run_id)
    if item is None:
        raise HTTPException(
            404,
            detail={
                "code": "kraken_sync_not_found",
                "message": "Der Kraken-Sync wurde nicht gefunden.",
            },
        )
    return _run(item)
