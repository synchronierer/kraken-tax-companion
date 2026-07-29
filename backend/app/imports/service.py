from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.core.entities import (
    AuditActorType,
    AuditEvent,
    ImportError,
    ImportSession,
    ImportStatus,
    RawImportRecord,
)
from app.core.identifiers import IdGenerator
from app.core.time import utc_now
from app.core.unit_of_work import UnitOfWork
from app.imports.context import ImportContext
from app.imports.errors import (
    ImportEngineError,
    PersistenceImportError,
    ValidationIssue,
)
from app.imports.hashing import (
    JsonObject,
    canonical_records_sha256,
    canonical_sha256,
    parse_json_object,
    verify_hash,
)
from app.imports.state_machine import transition
from app.imports.validation import ImportValidator


class ImportOutcome(StrEnum):
    SUCCESS = "success"
    DUPLICATE = "duplicate"
    FAILED = "failed"


@dataclass(frozen=True, kw_only=True)
class RawRecordInput:
    payload: JsonObject
    external_id: str | None = None
    technical_metadata: dict[str, Any] | None = None


@dataclass(frozen=True, kw_only=True)
class ImportResult:
    session_id: UUID
    content_hash: str | None
    skipped: bool
    outcome: ImportOutcome = ImportOutcome.SUCCESS
    accepted_count: int = 0
    rejected_count: int = 0
    errors: tuple[ValidationIssue, ...] = ()

    @property
    def import_hash(self) -> str | None:
        return self.content_hash


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

    def import_records(
        self,
        *,
        context: ImportContext,
        records: list[RawRecordInput],
        retry_failed: bool = False,
    ) -> ImportResult:
        """Persist an ordered generic batch and return expected failures as data."""

        try:
            return self._run_records(
                context=context, records=records, retry_failed=retry_failed
            )
        except ImportEngineError as error:
            error = self._record_failure_safely(context.session, error)
            return ImportResult(
                session_id=context.session.id,
                content_hash=context.session.import_hash,
                skipped=False,
                outcome=ImportOutcome.FAILED,
                accepted_count=0,
                rejected_count=max(1, len(records)),
                errors=(error.issue(),),
            )
        except Exception as error:
            wrapped: ImportEngineError = PersistenceImportError(
                code="persistence_error",
                description="The import could not be persisted.",
            )
            wrapped = self._record_failure_safely(
                context.session, wrapped, original=error
            )
            return ImportResult(
                session_id=context.session.id,
                content_hash=context.session.import_hash,
                skipped=False,
                outcome=ImportOutcome.FAILED,
                accepted_count=0,
                rejected_count=max(1, len(records)),
                errors=(wrapped.issue(),),
            )

    def _run_records(
        self,
        *,
        context: ImportContext,
        records: list[RawRecordInput],
        retry_failed: bool,
    ) -> ImportResult:
        session = context.session
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.import_sessions.add(session)
            self._audit(unit_of_work, context, "import.created", {})
            transition(session, ImportStatus.RECEIVED, self._clock())
            self._audit(unit_of_work, context, "import.started", {})
            session.received_count = len(records)
            transition(session, ImportStatus.VALIDATING, self._clock())
            if not records:
                raise ImportEngineError(
                    code="empty_data", description="Import data must not be empty."
                )
            for position, record in enumerate(records):
                try:
                    self._validator.validate(record.payload)
                except ImportEngineError as error:
                    error.record_position = position
                    raise
            transition(session, ImportStatus.HASHING, self._clock())
            import_hash = canonical_records_sha256(record.payload for record in records)
            session.import_hash = import_hash
            transition(session, ImportStatus.CHECKING_DUPLICATES, self._clock())
            previous = unit_of_work.import_sessions.find_by_hash(
                context.source, import_hash, exclude_id=session.id
            )
            if previous is not None and (
                previous.status is ImportStatus.COMPLETED
                or (previous.status is ImportStatus.FAILED and not retry_failed)
            ):
                session.skipped_count = len(records)
                transition(session, ImportStatus.COMPLETED, self._clock())
                self._audit(
                    unit_of_work,
                    context,
                    "import.duplicate_detected",
                    {"duplicate_of": str(previous.id), "import_hash": import_hash},
                )
                unit_of_work.commit()
                return ImportResult(
                    session_id=session.id,
                    content_hash=import_hash,
                    skipped=True,
                    outcome=ImportOutcome.DUPLICATE,
                    accepted_count=0,
                    rejected_count=0,
                )
            transition(session, ImportStatus.PERSISTING, self._clock())
            for position, item in enumerate(records):
                unit_of_work.raw_imports.add(
                    RawImportRecord(
                        import_session_id=session.id,
                        source=context.source,
                        content_hash=canonical_sha256(item.payload),
                        payload=item.payload,
                        sequence_number=position,
                        external_id=item.external_id,
                        technical_metadata=item.technical_metadata or {},
                    )
                )
            unit_of_work.flush()
            session.persisted_count = len(records)
            transition(session, ImportStatus.COMPLETED, self._clock())
            self._audit(
                unit_of_work,
                context,
                "import.completed",
                {"import_hash": import_hash, "record_count": len(records)},
            )
            unit_of_work.commit()
            return ImportResult(
                session_id=session.id,
                content_hash=import_hash,
                skipped=False,
                outcome=ImportOutcome.SUCCESS,
                accepted_count=len(records),
                rejected_count=0,
            )

    def _audit(
        self,
        unit_of_work: UnitOfWork,
        context: ImportContext,
        event_type: str,
        metadata: dict[str, Any],
    ) -> None:
        unit_of_work.audit.add(
            AuditEvent(
                occurred_at=self._clock(),
                event_type=event_type,
                entity_type="ImportSession",
                entity_id=context.session.id,
                actor_type=context.actor_type,
                actor_id=context.actor_id,
                metadata={
                    **metadata,
                    "correlation_id": str(context.correlation_id),
                    "source": context.source,
                },
            )
        )

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
            recorded = self._record_failure_safely(session, error)
            if recorded is not error:
                raise recorded from error
            raise
        except Exception as error:
            wrapped: ImportEngineError = PersistenceImportError(
                code="unexpected_import_error",
                description=(
                    "The import failed because of an unexpected "
                    "infrastructure error."
                ),
            )
            wrapped = self._record_failure_safely(session, wrapped, original=error)
            raise wrapped from error

    def _record_failure_safely(
        self,
        session: ImportSession,
        error: ImportEngineError,
        *,
        original: Exception | None = None,
    ) -> ImportEngineError:
        try:
            self._record_failure(session, error, original=original)
        except Exception:
            return PersistenceImportError(
                code="failure_evidence_persistence_error",
                description=(
                    "The import failed and its failure evidence could not be persisted."
                ),
            )
        return error

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
            self._audit(unit_of_work, context, "import.created", {})
            transition(session, ImportStatus.RECEIVED, self._clock())
            self._audit(unit_of_work, context, "import.started", {})
            session.received_count = 1

            transition(session, ImportStatus.VALIDATING, self._clock())
            payload = parse_json_object(raw_data)
            self._validator.validate(payload)

            transition(session, ImportStatus.HASHING, self._clock())
            content_hash = canonical_sha256(payload)
            session.import_hash = content_hash
            verify_hash(content_hash, expected_hash)

            transition(session, ImportStatus.CHECKING_DUPLICATES, self._clock())
            duplicate = unit_of_work.raw_imports.find_by_hash(
                context.source, content_hash
            )
            if duplicate is not None:
                session.skipped_count = 1
                transition(session, ImportStatus.COMPLETED, self._clock())
                self._audit(
                    unit_of_work,
                    context,
                    "import.duplicate_detected",
                    {"duplicate_of": str(duplicate.import_session_id)},
                )
                unit_of_work.commit()
                return ImportResult(
                    session_id=session.id,
                    content_hash=content_hash,
                    skipped=True,
                    outcome=ImportOutcome.DUPLICATE,
                    accepted_count=0,
                    rejected_count=0,
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
            self._audit(
                unit_of_work,
                context,
                "import.completed",
                {"import_hash": content_hash, "record_count": 1},
            )
            unit_of_work.commit()
            return ImportResult(
                session_id=session.id,
                content_hash=content_hash,
                skipped=False,
                outcome=ImportOutcome.SUCCESS,
                accepted_count=1,
                rejected_count=0,
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
        session.error_summary = error.description
        exception = original if original is not None else error
        import_error = ImportError(
            occurred_at=occurred_at,
            import_session_id=session.id,
            category=error.category,
            error_code=error.code,
            description=error.description,
            original_exception=f"{type(exception).__name__}: {exception}",
            affected_record=error.affected_record,
        )
        with self._unit_of_work_factory() as recovery:
            recovery.import_sessions.add(session)
            recovery.import_errors.add(import_error)
            for event_type, metadata in (
                ("import.created", {}),
                ("import.started", {}),
                (
                    "import.failed",
                    {
                        "error_code": error.code,
                        "import_hash": session.import_hash,
                    },
                ),
            ):
                recovery.audit.add(
                    AuditEvent(
                        occurred_at=occurred_at,
                        event_type=event_type,
                        entity_type="ImportSession",
                        entity_id=session.id,
                        actor_type=session.actor_type,
                        actor_id=session.actor_id,
                        metadata={
                            **metadata,
                            "correlation_id": str(session.correlation_id),
                            "source": session.source,
                        },
                    )
                )
            recovery.commit()
