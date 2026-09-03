from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.core.identifiers import new_id
from app.core.time import require_utc, utc_now


class SyncStatus(StrEnum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(kw_only=True)
class IncrementalSyncRun:
    account_scope: str
    sync_kind: str
    status: SyncStatus
    requested_from: datetime
    requested_to: datetime
    lookback_seconds: int
    started_at: datetime
    id: UUID = field(default_factory=new_id)
    previous_success_id: UUID | None = None
    import_session_id: UUID | None = None
    transformation_run_id: UUID | None = None
    ended_at: datetime | None = None
    fetched_pages: int = 0
    provider_records: int = 0
    unique_records: int = 0
    known_records: int = 0
    new_raw_records: int = 0
    new_domain_objects: int = 0
    review_count: int = 0
    error_count: int = 0
    ledger_id_digest: str | None = None
    error_code: str | None = None
    error_summary: str | None = None
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.account_scope = self.account_scope.strip()
        self.sync_kind = self.sync_kind.strip()
        if not self.account_scope or not self.sync_kind:
            raise ValueError("Sync scope and kind must not be empty.")
        self.requested_from = require_utc(self.requested_from)
        self.requested_to = require_utc(self.requested_to)
        self.started_at = require_utc(self.started_at)
        self.created_at = require_utc(self.created_at)
        if self.requested_to <= self.requested_from:
            raise ValueError("Sync end must be after start.")
        if self.lookback_seconds <= 0:
            raise ValueError("Sync lookback must be positive.")
        if self.ended_at is not None:
            self.ended_at = require_utc(self.ended_at)
        for name in (
            "fetched_pages",
            "provider_records",
            "unique_records",
            "known_records",
            "new_raw_records",
            "new_domain_objects",
            "review_count",
            "error_count",
        ):
            if getattr(self, name) < 0:
                raise ValueError("Sync counters must not be negative.")


def complete_sync(run: IncrementalSyncRun, ended_at: datetime) -> None:
    if run.status is not SyncStatus.PROCESSING:
        raise ValueError("Only a processing sync can complete.")
    terminal_at = require_utc(ended_at)
    if terminal_at < run.started_at:
        raise ValueError("Sync end must not precede its start.")
    run.status = SyncStatus.COMPLETED
    run.ended_at = terminal_at
    run.error_code = None
    run.error_summary = None


def fail_sync(
    run: IncrementalSyncRun, ended_at: datetime, code: str, summary: str
) -> None:
    if run.status is not SyncStatus.PROCESSING:
        raise ValueError("Only a processing sync can fail.")
    terminal_at = require_utc(ended_at)
    if terminal_at < run.started_at or not code.strip() or not summary.strip():
        raise ValueError("Failed sync evidence is invalid.")
    run.status = SyncStatus.FAILED
    run.ended_at = terminal_at
    run.error_code = code.strip()
    run.error_summary = summary.strip()
    run.error_count = max(1, run.error_count)
