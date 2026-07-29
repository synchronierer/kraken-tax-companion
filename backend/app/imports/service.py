from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.core.entities import (
    AuditActorType,
    AuditEvent,
    ErrorCategory,
    ImportError,
    ImportSession,
    ImportStatus,
    RawImportRecord,
)
from app.core.identifiers import IdGenerator
from app.core.time import utc_now
from app.core.unit_of_work import UnitOfWork
from app.imports.context import ImportContext
from app.imports.errors import ImportEngineError
from app.imports.hashing import canonical_sha256, parse_json_object, verify_hash
from app.imports.state_machine import transition
from app.imports.validation import ImportValidator


@dataclass(frozen=True, kw_only=True)
class ImportResult:
    session_id: UUID
    content_hash: str | None
    skipped: bool


class ImportService:
    def __init__(
        self,
        *,
        unit_of_work_factory: Callable[[], UnitOfWork],
        id_generator: IdGenerator,
        validator: ImportValidator,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._id_generator = id_generator
        self._validator = validator
        self._clock = clock

    def import_json(
        self,
        *,
        raw_data: str | bytes,
        source: str,
        version: str,
        actor_type: AuditActorType,
        actor_id: str,
        expected_hash: str | None = None,
    ) -> ImportResult:
        started_at = self._clock()
        session = ImportSession(
            source=source,
            version=version,
            status=ImportStatus.CREATED,
            started_at=started_at,
            correlation_id=self._id_generator.new(),
            actor_type=actor_type,
            actor_id=actor_id,
        )
        context = ImportContext(
            session=session,
            source=source,
            version=version,
            received_at=started_at,
            actor_type=actor_type,
            actor_id=actor_id,
            correlation_id=session.correlation_id,
        )
        try:
            return self._run_import(
                context=context,
                raw_data=raw_data,
                expected_hash=expected_hash,
            )
        except ImportEngineError as error:
            self._record_failure(session, error)
            raise
        except Exception as error:
            wrapped = ImportEngineError(
                code="unexpected_import_error",
                description=(
                    "The import failed because of an unexpected "
                    "infrastructure error."
                ),
            )
            self._record_failure(session, wrapped, original=error)
            raise wrapped from error

    def _run_import(
        self,
        *,
        context: ImportContext,
        raw_data: str | bytes,
        expected_hash: str | None,
    ) -> ImportResult:
        session = context.session
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.import_sessions.add(session)
            transition(session, ImportStatus.RECEIVED, self._clock())
            session.received_count = 1

            transition(session, ImportStatus.VALIDATING, self._clock())
            payload = parse_json_object(raw_data)
            self._validator.validate(payload)

            transition(session, ImportStatus.HASHING, self._clock())
            content_hash = canonical_sha256(payload)
            verify_hash(content_hash, expected_hash)

            transition(session, ImportStatus.CHECKING_DUPLICATES, self._clock())
            duplicate = unit_of_work.raw_imports.find_by_hash(
                context.source, content_hash
            )
            if duplicate is not None:
                session.skipped_count = 1
                transition(session, ImportStatus.COMPLETED, self._clock())
                unit_of_work.commit()
                return ImportResult(
                    session_id=session.id,
                    content_hash=content_hash,
                    skipped=True,
                )

            transition(session, ImportStatus.PERSISTING, self._clock())
            record = RawImportRecord(
                import_session_id=session.id,
                source=context.source,
                content_hash=content_hash,
                payload=payload,
            )
            unit_of_work.raw_imports.add(record)
            unit_of_work.audit.add(
                AuditEvent(
                    occurred_at=self._clock(),
                    event_type="raw_import.persisted",
                    entity_type="RawImportRecord",
                    entity_id=record.id,
                    actor_type=context.actor_type,
                    actor_id=context.actor_id,
                    metadata={
                        "content_hash": content_hash,
                        "correlation_id": str(context.correlation_id),
                        "source": context.source,
                    },
                )
            )
            unit_of_work.flush()
            session.persisted_count = 1
            transition(session, ImportStatus.COMPLETED, self._clock())
            unit_of_work.commit()
            return ImportResult(
                session_id=session.id,
                content_hash=content_hash,
                skipped=False,
            )

    def _record_failure(
        self,
        session: ImportSession,
        error: ImportEngineError,
        *,
        original: Exception | None = None,
    ) -> None:
        occurred_at = self._clock()
        transition(session, ImportStatus.FAILED, occurred_at)
        exception = original if original is not None else error
        import_error = ImportError(
            occurred_at=occurred_at,
            import_session_id=session.id,
            category=ErrorCategory.IMPORT,
            error_code=error.code,
            description=error.description,
            original_exception=f"{type(exception).__name__}: {exception}",
            affected_record=error.affected_record,
        )
        with self._unit_of_work_factory() as recovery:
            recovery.import_sessions.add(session)
            recovery.import_errors.add(import_error)
            recovery.commit()
