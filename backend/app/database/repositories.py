from collections.abc import Sequence
from typing import Any, TypeVar, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.entities import AuditEvent, ImportError, ImportSession, RawImportRecord
from app.database.mappings import import_sessions, raw_import_records

EntityT = TypeVar("EntityT")


class SqlAlchemyRepository[EntityT]:
    def __init__(self, session: Session, model: type[EntityT]) -> None:
        self._session = session
        self._model = model

    def add(self, entity: EntityT) -> None:
        self._session.add(entity)

    def get(self, entity_id: UUID) -> EntityT | None:
        return self._session.get(self._model, entity_id)

    def list(self) -> Sequence[EntityT]:
        return tuple(self._session.scalars(select(self._model)).all())


class SqlAlchemyRawImportRepository(SqlAlchemyRepository[RawImportRecord]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, RawImportRecord)

    def find_by_hash(self, source: str, content_hash: str) -> RawImportRecord | None:
        statement = select(RawImportRecord).where(
            raw_import_records.c.source == source,
            raw_import_records.c.content_hash == content_hash,
        )
        return self._session.scalar(statement)

    def list_by_import_sessions(
        self, import_session_ids: Sequence[UUID]
    ) -> Sequence[RawImportRecord]:
        statement = (
            select(RawImportRecord)
            .where(raw_import_records.c.import_session_id.in_(import_session_ids))
            .order_by(
                raw_import_records.c.created_at,
                raw_import_records.c.sequence_number,
            )
        )
        return tuple(self._session.scalars(statement))

    def list_by_external_id(self, external_id: str) -> Sequence[RawImportRecord]:
        statement = select(RawImportRecord).where(
            raw_import_records.c.external_id == external_id
        )
        return tuple(self._session.scalars(statement))

    def find_by_canonical_key(self, canonical_key: str) -> RawImportRecord | None:
        statement = select(RawImportRecord).where(
            raw_import_records.c.canonical_key == canonical_key
        )
        return self._session.scalar(statement)


class SqlAlchemyImportSessionRepository(SqlAlchemyRepository[ImportSession]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, ImportSession)

    def find_by_hash(
        self, source: str, import_hash: str, *, exclude_id: UUID | None = None
    ) -> ImportSession | None:
        statement = select(ImportSession).where(
            import_sessions.c.source == source,
            import_sessions.c.import_hash == import_hash,
        )
        if exclude_id is not None:
            statement = statement.where(import_sessions.c.id != exclude_id)
        statement = statement.order_by(import_sessions.c.started_at.desc())
        return self._session.scalars(statement).first()


class SqlAlchemyAuditRepository(SqlAlchemyRepository[AuditEvent]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, AuditEvent)


class SqlAlchemyImportErrorRepository(SqlAlchemyRepository[ImportError]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, ImportError)


class SqlAlchemyStableProjectionRepository[EntityT](SqlAlchemyRepository[EntityT]):
    def find_by_stable_key(self, stable_key: str) -> EntityT | None:
        stable_attribute: Any = cast(Any, self._model).stable_key
        return self._session.scalar(
            select(self._model).where(stable_attribute == stable_key)
        )
