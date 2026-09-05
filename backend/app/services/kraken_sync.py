from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.kraken.ledger import (
    LEDGER_ASSET_MAPPING_VERSION,
    LEDGER_NORMALIZATION_VERSION,
    canonical_fingerprint,
    canonical_from_api,
)
from app.adapters.kraken.transformation import KrakenTransformationService
from app.core.entities import (
    AuditActorType,
    ImportSession,
    ImportStatus,
    RawImportRecord,
)
from app.core.identifiers import Uuid4IdGenerator
from app.core.incremental_sync import (
    IncrementalSyncRun,
    SyncStatus,
    complete_sync,
    fail_sync,
)
from app.core.time import utc_now
from app.database.mappings import kraken_sync_runs, raw_import_records
from app.database.unit_of_work import SqlAlchemyUnitOfWork
from app.imports.context import ImportContext
from app.imports.service import ImportOutcome, ImportService, RawRecordInput
from app.imports.validation import RequiredFieldsValidator
from app.infrastructure.kraken_private import KrakenPrivateError, LedgerPreview

ACCOUNT_SCOPE = "default"
SYNC_KIND = "spot_ledger"


class LedgerClient(Protocol):
    def ledger_preview(
        self,
        *,
        start: datetime | None,
        end: datetime | None,
        asset: str | None,
        ledger_type: str,
        diagnostic_limit: int,
    ) -> LedgerPreview: ...


class KrakenSyncError(Exception):
    def __init__(self, code: str, message: str, *, run_id: UUID | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.run_id = code, message, run_id


@dataclass(frozen=True)
class KrakenSyncPlan:
    requested_from: datetime
    requested_to: datetime
    lookback_seconds: int
    last_successful: IncrementalSyncRun | None
    processing: IncrementalSyncRun | None


class KrakenSyncService:
    def __init__(
        self,
        *,
        db: Session,
        client_factory: Callable[[], LedgerClient],
        initial_start: datetime,
        lookback_seconds: int,
        settlement_lag_seconds: int,
        stale_seconds: int,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.db, self.client_factory, self.initial_start = (
            db,
            client_factory,
            initial_start,
        )
        self.lookback_seconds = lookback_seconds
        self.settlement_lag_seconds = settlement_lag_seconds
        self.stale_seconds = stale_seconds
        self.clock = clock

    def latest_successful_sync(self) -> IncrementalSyncRun | None:
        return self.db.scalar(
            select(IncrementalSyncRun)
            .where(
                kraken_sync_runs.c.account_scope == ACCOUNT_SCOPE,
                kraken_sync_runs.c.sync_kind == SYNC_KIND,
                kraken_sync_runs.c.status == SyncStatus.COMPLETED,
            )
            .order_by(
                kraken_sync_runs.c.requested_to.desc(),
                kraken_sync_runs.c.started_at.desc(),
                kraken_sync_runs.c.id.desc(),
            )
        )

    def _processing(self) -> IncrementalSyncRun | None:
        return self.db.scalar(
            select(IncrementalSyncRun)
            .where(
                kraken_sync_runs.c.account_scope == ACCOUNT_SCOPE,
                kraken_sync_runs.c.sync_kind == SYNC_KIND,
                kraken_sync_runs.c.status == SyncStatus.PROCESSING,
            )
            .order_by(kraken_sync_runs.c.started_at.desc())
        )

    def plan_sync(self) -> KrakenSyncPlan:
        now = self.clock()
        end = now - timedelta(seconds=self.settlement_lag_seconds)
        previous = self.latest_successful_sync()
        start = (
            self.initial_start
            if previous is None
            else max(
                self.initial_start,
                previous.requested_to - timedelta(seconds=self.lookback_seconds),
            )
        )
        return KrakenSyncPlan(
            start, end, self.lookback_seconds, previous, self._processing()
        )

    def list_sync_runs(self) -> tuple[IncrementalSyncRun, ...]:
        return tuple(
            self.db.scalars(
                select(IncrementalSyncRun).order_by(
                    kraken_sync_runs.c.started_at.desc(), kraken_sync_runs.c.id.desc()
                )
            )
        )

    def run_sync(self) -> IncrementalSyncRun:
        plan = self.plan_sync()
        if plan.requested_to <= plan.requested_from:
            raise KrakenSyncError(
                "kraken_sync_window_invalid", "Der geplante Sync-Zeitraum ist leer."
            )
        self._recover_or_reject_processing(plan.processing)
        previous_success_id = (
            plan.last_successful.id if plan.last_successful is not None else None
        )
        run = IncrementalSyncRun(
            account_scope=ACCOUNT_SCOPE,
            sync_kind=SYNC_KIND,
            status=SyncStatus.PROCESSING,
            requested_from=plan.requested_from,
            requested_to=plan.requested_to,
            lookback_seconds=self.lookback_seconds,
            previous_success_id=previous_success_id,
            started_at=self.clock(),
        )
        try:
            self.db.add(run)
            self.db.commit()
        except IntegrityError as error:
            self.db.rollback()
            raise KrakenSyncError(
                "kraken_sync_already_running", "Ein Kraken-Sync läuft bereits."
            ) from error
        try:
            client = self.client_factory()
            preview = client.ledger_preview(
                start=run.requested_from,
                end=run.requested_to,
                asset=None,
                ledger_type="all",
                diagnostic_limit=0,
            )
            self._validate_preview(preview)
            confirmation = client.ledger_preview(
                start=run.requested_from,
                end=run.requested_to,
                asset=None,
                ledger_type="all",
                diagnostic_limit=0,
            )
            self._validate_preview(confirmation)
            first_fingerprints = tuple(
                canonical_fingerprint(canonical_from_api(item))
                for item in preview.records
            )
            confirmation_fingerprints = tuple(
                canonical_fingerprint(canonical_from_api(item))
                for item in confirmation.records
            )
            if (
                preview.stable_ledger_id_digest != confirmation.stable_ledger_id_digest
                or first_fingerprints != confirmation_fingerprints
            ):
                raise KrakenSyncError(
                    "kraken_sync_provider_changed",
                    "Das Kraken-Ledger änderte sich während der Pagination.",
                )
            self._persist(
                run,
                replace(
                    confirmation,
                    fetched_pages=preview.fetched_pages + confirmation.fetched_pages,
                ),
            )
            return run
        except KrakenSyncError as error:
            self._record_failure(run.id, error.code, error.message)
            error.run_id = run.id
            raise
        except KrakenPrivateError as error:
            self._record_failure(run.id, error.code, error.message)
            raise KrakenSyncError(error.code, error.message, run_id=run.id) from error
        except Exception as error:
            self._record_failure(
                run.id,
                "kraken_sync_internal_failure",
                "Der Kraken-Sync wurde atomar abgebrochen.",
            )
            raise KrakenSyncError(
                "kraken_sync_internal_failure",
                "Der Kraken-Sync wurde atomar abgebrochen.",
                run_id=run.id,
            ) from error

    def _recover_or_reject_processing(self, active: IncrementalSyncRun | None) -> None:
        if active is None:
            return
        if self.clock() - active.started_at <= timedelta(seconds=self.stale_seconds):
            raise KrakenSyncError(
                "kraken_sync_already_running",
                "Ein Kraken-Sync läuft bereits.",
                run_id=active.id,
            )
        fail_sync(
            active,
            self.clock(),
            "kraken_sync_stale_recovered",
            "Ein veralteter PROCESSING-Lauf wurde sicher beendet.",
        )
        self.db.commit()

    @staticmethod
    def _validate_preview(preview: LedgerPreview) -> None:
        if preview.conflicting_duplicate_ids:
            raise KrakenSyncError(
                "kraken_sync_conflicting_ledger_id",
                "Kraken lieferte widersprüchliche Ledger-IDs.",
            )
        if preview.malformed_entries:
            raise KrakenSyncError(
                "kraken_sync_malformed_records",
                "Kraken lieferte fehlerhafte Ledger-Datensätze.",
            )
        if not preview.pagination_complete:
            raise KrakenSyncError(
                "kraken_sync_incomplete_pagination",
                "Die Kraken-Pagination ist unvollständig.",
            )
        if not preview.ready_for_import:
            raise KrakenSyncError(
                "kraken_sync_provider_count_mismatch",
                "Die Kraken-Gesamtzahl ist inkonsistent.",
            )

    def _persist(self, run: IncrementalSyncRun, preview: LedgerPreview) -> None:
        records = tuple(canonical_from_api(item) for item in preview.records)
        inputs: list[RawRecordInput] = []
        source_sessions: set[UUID] = set()
        for item in records:
            existing = self.db.scalar(
                select(RawImportRecord).where(
                    raw_import_records.c.canonical_key == item.canonical_key
                )
            )
            fingerprint = canonical_fingerprint(item)
            if existing is not None:
                if (
                    str(
                        existing.technical_metadata.get(
                            "canonical_fingerprint", existing.content_hash
                        )
                    )
                    != fingerprint
                ):
                    raise KrakenSyncError(
                        "canonical_record_conflict",
                        "Eine bekannte Kraken-Ledger-ID hat abweichenden Inhalt.",
                    )
                source_sessions.add(existing.import_session_id)
                continue
            inputs.append(
                RawRecordInput(
                    payload=item.import_payload(),
                    external_id=f"kraken:ledger:{item.ledger_id}",
                    canonical_key=item.canonical_key,
                    technical_metadata={
                        "source_kind": item.source_kind.value,
                        "canonical_fingerprint": fingerprint,
                        "normalization_version": LEDGER_NORMALIZATION_VERSION,
                        "asset_mapping_version": LEDGER_ASSET_MAPPING_VERSION,
                        "canonical_asset": {
                            "raw_asset": item.asset_raw,
                            "normalized_asset": item.asset_normalized,
                            "product_marker": item.product_marker,
                            "product_variant": item.product_variant,
                            "is_unambiguous": item.asset_mapping_known,
                        },
                    },
                )
            )
        run.fetched_pages, run.provider_records = (
            preview.fetched_pages,
            preview.received_total,
        )
        run.unique_records, run.known_records = (
            preview.unique_total,
            len(records) - len(inputs),
        )
        run.new_raw_records, run.ledger_id_digest = (
            len(inputs),
            preview.stable_ledger_id_digest,
        )
        if inputs:
            now = self.clock()
            session = ImportSession(
                source="kraken-ledgers",
                version=LEDGER_NORMALIZATION_VERSION,
                status=ImportStatus.CREATED,
                started_at=now,
                correlation_id=Uuid4IdGenerator().new(),
                actor_type=AuditActorType.USER,
                actor_id="local-user",
            )
            context = ImportContext(
                session=session,
                source=session.source,
                version=session.version,
                received_at=now,
                actor_type=session.actor_type,
                actor_id=session.actor_id,
                correlation_id=session.correlation_id,
                source_name="Kraken Incremental Sync",
            )
            sf: sessionmaker[Session] = sessionmaker(
                bind=self.db.get_bind(), expire_on_commit=False
            )

            def factory() -> SqlAlchemyUnitOfWork:
                return SqlAlchemyUnitOfWork(sf, external_session=self.db)

            result = ImportService(
                unit_of_work_factory=factory,
                id_generator=Uuid4IdGenerator(),
                validator=RequiredFieldsValidator(),
            ).import_records(context=context, records=inputs)
            if result.outcome is ImportOutcome.FAILED:
                raise KrakenSyncError(
                    "kraken_sync_import_failed", "Der Kraken-Import ist fehlgeschlagen."
                )
            run.import_session_id = session.id
            source_sessions.add(session.id)
            transformed = KrakenTransformationService(
                unit_of_work_factory=factory
            ).transform(
                import_session_ids=(session.id,),
                context_import_session_ids=tuple(
                    item for item in source_sessions if item != session.id
                ),
                actor_id="local-user",
            )
            if transformed.status.value == "failed":
                raise KrakenSyncError(
                    "kraken_sync_transformation_failed",
                    "Die Kraken-Transformation ist fehlgeschlagen.",
                )
            run.transformation_run_id = transformed.run_id
            run.new_domain_objects = (
                transformed.acquisitions
                + transformed.disposals
                + transformed.trade_executions
                + transformed.fee_events
                + transformed.valuation_requirements
            )
            run.review_count = transformed.review_cases + transformed.conflicts
            run.error_count = transformed.conflicts
        complete_sync(run, self.clock())
        self.db.commit()

    def _record_failure(self, run_id: UUID, code: str, summary: str) -> None:
        self.db.rollback()
        failed = self.db.get(IncrementalSyncRun, run_id)
        if failed is not None and failed.status is SyncStatus.PROCESSING:
            fail_sync(failed, self.clock(), code, summary)
            self.db.commit()
