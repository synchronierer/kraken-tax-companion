from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app import models
from app.core.entities import (
    AuditActorType,
    AuditEvent,
    ImportError,
    ImportSession,
    ImportStatus,
    RawImportRecord,
)
from app.core.identifiers import Uuid4IdGenerator, new_id
from app.database.base import Base
from app.database.unit_of_work import SqlAlchemyUnitOfWork
from app.imports.context import ImportContext
from app.imports.errors import (
    ImportEngineError,
    ImportIntegrityError,
    ImportValidationError,
)
from app.imports.hashing import (
    canonical_json,
    canonical_sha256,
    parse_json_object,
    verify_hash,
)
from app.imports.service import ImportService
from app.imports.state_machine import InvalidImportTransition, transition
from app.imports.validation import RequiredFieldsValidator

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)


def session_factory() -> sessionmaker[Session]:
    models.configure_mappings()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def service(
    factory: sessionmaker[Session],
    validator: RequiredFieldsValidator | None = None,
) -> ImportService:
    return ImportService(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(factory),
        id_generator=Uuid4IdGenerator(),
        validator=validator or RequiredFieldsValidator(),
        clock=lambda: NOW,
    )


def import_session(status: ImportStatus = ImportStatus.CREATED) -> ImportSession:
    return ImportSession(
        source="generic-json",
        version="1",
        status=status,
        started_at=NOW,
        correlation_id=new_id(),
        actor_type=AuditActorType.SYSTEM,
        actor_id="test-suite",
    )


def test_canonical_hash_is_deterministic_and_order_independent() -> None:
    first = {"asset": "BTC", "amount": "1.00", "nested": {"b": 2, "a": 1}}
    reordered = {"nested": {"a": 1, "b": 2}, "amount": "1.00", "asset": "BTC"}

    assert canonical_sha256(first) == canonical_sha256(first)
    assert canonical_sha256(first) == canonical_sha256(reordered)
    assert canonical_sha256(first) != canonical_sha256({"asset": "ETH"})
    assert canonical_json({"ä": "€"}).decode() == '{"ä":"€"}'


@pytest.mark.parametrize(
    ("raw_data", "code"),
    [
        ("", "empty_data"),
        ("  ", "empty_data"),
        (b"\xff", "invalid_json"),
        ("{invalid", "invalid_json"),
        ("[]", "invalid_root"),
    ],
)
def test_parse_json_rejects_invalid_input(raw_data: str | bytes, code: str) -> None:
    with pytest.raises(ImportValidationError) as result:
        parse_json_object(raw_data)
    assert result.value.code == code


def test_canonical_json_rejects_unsupported_values() -> None:
    with pytest.raises(ImportValidationError, match="unsupported"):
        canonical_json({"amount": Decimal("1")})
    with pytest.raises(ImportValidationError, match="unsupported"):
        canonical_json({"amount": float("nan")})


def test_hash_verification() -> None:
    content_hash = canonical_sha256({"record": 1})
    verify_hash(content_hash, None)
    verify_hash(content_hash, content_hash.upper())
    with pytest.raises(ImportIntegrityError) as result:
        verify_hash(content_hash, "0" * 64)
    assert result.value.code == "hash_mismatch"


def test_required_fields_validator() -> None:
    validator = RequiredFieldsValidator(frozenset({"records", "source_id"}))
    validator.validate({"records": [], "source_id": "batch-1"})
    with pytest.raises(ImportValidationError) as result:
        validator.validate({"records": []})
    assert result.value.code == "missing_required_fields"
    assert result.value.affected_record == {"records": []}


def test_state_machine_accepts_pipeline_and_rejects_invalid_transition() -> None:
    imported = import_session()
    for status in (
        ImportStatus.RECEIVED,
        ImportStatus.VALIDATING,
        ImportStatus.HASHING,
        ImportStatus.CHECKING_DUPLICATES,
        ImportStatus.PERSISTING,
        ImportStatus.COMPLETED,
    ):
        transition(imported, status, NOW)
    assert imported.ended_at == NOW
    with pytest.raises(InvalidImportTransition):
        transition(imported, ImportStatus.FAILED, NOW)


def test_state_machine_supports_failure_and_cancellation() -> None:
    failed = import_session()
    transition(failed, ImportStatus.FAILED, NOW)
    assert failed.ended_at == NOW
    cancelled = import_session()
    transition(cancelled, ImportStatus.CANCELLED, NOW)
    assert cancelled.status is ImportStatus.CANCELLED


@pytest.mark.parametrize(
    "field_name", ["source", "version", "actor_type", "actor_id", "correlation_id"]
)
def test_import_context_requires_matching_session(field_name: str) -> None:
    imported = import_session()
    values = {
        "session": imported,
        "source": imported.source,
        "version": imported.version,
        "received_at": NOW,
        "actor_type": imported.actor_type,
        "actor_id": imported.actor_id,
        "correlation_id": imported.correlation_id,
    }
    values[field_name] = new_id() if field_name == "correlation_id" else "different"
    with pytest.raises(ValueError, match="must match"):
        ImportContext(**values)  # type: ignore[arg-type]


def test_import_service_persists_raw_data_and_audit() -> None:
    factory = session_factory()
    result = service(factory).import_json(
        raw_data='{"records":[{"value":1}],"source_id":"batch-1"}',
        source="generic-json",
        version="1",
        actor_type=AuditActorType.SYSTEM,
        actor_id="test-suite",
    )

    assert result.skipped is False
    assert result.content_hash is not None
    with factory() as database:
        imported = database.get(ImportSession, result.session_id)
        assert imported is not None
        assert imported.status is ImportStatus.COMPLETED
        assert imported.received_count == 1
        assert imported.persisted_count == 1
        assert imported.skipped_count == 0
        assert database.scalar(select(func.count()).select_from(RawImportRecord)) == 1
        audit = database.scalar(select(AuditEvent))
        assert audit is not None
        assert audit.event_type == "raw_import.persisted"
        assert audit.metadata["content_hash"] == result.content_hash


def test_duplicate_import_is_skipped_without_duplicate_record_or_audit() -> None:
    factory = session_factory()
    engine = service(factory)
    arguments = {
        "raw_data": '{"b":2,"a":1}',
        "source": "generic-json",
        "version": "1",
        "actor_type": AuditActorType.USER,
        "actor_id": "user-1",
    }
    first = engine.import_json(**arguments)  # type: ignore[arg-type]
    second = engine.import_json(
        raw_data='{"a":1,"b":2}',
        source="generic-json",
        version="1",
        actor_type=AuditActorType.USER,
        actor_id="user-1",
    )

    assert first.content_hash == second.content_hash
    assert second.skipped is True
    with factory() as database:
        assert database.scalar(select(func.count()).select_from(RawImportRecord)) == 1
        assert database.scalar(select(func.count()).select_from(AuditEvent)) == 1
        assert database.scalar(select(func.count()).select_from(ImportSession)) == 2
        duplicate_session = database.get(ImportSession, second.session_id)
        assert duplicate_session is not None
        assert duplicate_session.status is ImportStatus.COMPLETED
        assert duplicate_session.skipped_count == 1
        assert duplicate_session.persisted_count == 0


def test_validation_failure_rolls_back_and_records_import_error() -> None:
    factory = session_factory()
    engine = service(factory, RequiredFieldsValidator(frozenset({"records"})))
    with pytest.raises(ImportValidationError):
        engine.import_json(
            raw_data='{"source_id":"batch-1"}',
            source="generic-json",
            version="1",
            actor_type=AuditActorType.SYSTEM,
            actor_id="test-suite",
        )

    with factory() as database:
        assert database.scalar(select(func.count()).select_from(RawImportRecord)) == 0
        assert database.scalar(select(func.count()).select_from(AuditEvent)) == 0
        imported = database.scalar(select(ImportSession))
        error = database.scalar(select(ImportError))
        assert imported is not None and imported.status is ImportStatus.FAILED
        assert error is not None and error.error_code == "missing_required_fields"
        assert error.affected_record == {"source_id": "batch-1"}


def test_expected_hash_failure_is_recorded() -> None:
    factory = session_factory()
    with pytest.raises(ImportIntegrityError):
        service(factory).import_json(
            raw_data='{"record":1}',
            source="generic-json",
            version="1",
            actor_type=AuditActorType.SYSTEM,
            actor_id="test-suite",
            expected_hash="0" * 64,
        )
    with factory() as database:
        error = database.scalar(select(ImportError))
        assert error is not None and error.error_code == "hash_mismatch"


class FailingValidator:
    def validate(self, payload: dict[str, object]) -> None:
        del payload
        raise RuntimeError("storage unavailable")


def test_unexpected_failure_rolls_back_and_is_wrapped() -> None:
    factory = session_factory()
    engine = ImportService(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(factory),
        id_generator=Uuid4IdGenerator(),
        validator=FailingValidator(),
        clock=lambda: NOW,
    )
    with pytest.raises(ImportEngineError) as result:
        engine.import_json(
            raw_data='{"record":1}',
            source="generic-json",
            version="1",
            actor_type=AuditActorType.SYSTEM,
            actor_id="test-suite",
        )
    assert result.value.code == "unexpected_import_error"
    with factory() as database:
        assert database.scalar(select(func.count()).select_from(RawImportRecord)) == 0
        error = database.scalar(select(ImportError))
        assert error is not None
        assert error.original_exception == "RuntimeError: storage unavailable"


def test_import_session_rejects_negative_counters() -> None:
    with pytest.raises(ValueError, match="received_count must not be negative"):
        ImportSession(
            source="generic-json",
            version="1",
            status=ImportStatus.CREATED,
            started_at=NOW,
            correlation_id=new_id(),
            actor_type=AuditActorType.SYSTEM,
            actor_id="test-suite",
            received_count=-1,
        )


def test_sqlalchemy_repositories_get_and_list() -> None:
    factory = session_factory()
    imported = import_session()
    with SqlAlchemyUnitOfWork(factory) as unit_of_work:
        unit_of_work.import_sessions.add(imported)
        unit_of_work.commit()

    with SqlAlchemyUnitOfWork(factory) as unit_of_work:
        assert unit_of_work.import_sessions.get(imported.id) == imported
        assert unit_of_work.import_sessions.list() == (imported,)


def test_unit_of_work_guards_lifecycle_and_supports_rollback() -> None:
    unit_of_work = SqlAlchemyUnitOfWork(session_factory())
    with pytest.raises(RuntimeError, match="must be entered"):
        unit_of_work.flush()
    with pytest.raises(RuntimeError, match="must be entered"):
        unit_of_work.commit()
    with pytest.raises(RuntimeError, match="must be entered"):
        unit_of_work.rollback()

    with unit_of_work:
        unit_of_work.rollback()

    unit_of_work.__exit__(None, None, None)
