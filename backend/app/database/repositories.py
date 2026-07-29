from collections.abc import Sequence
from typing import TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.entities import AuditEvent, ImportError, ImportSession, RawImportRecord
from app.database.mappings import raw_import_records

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


class SqlAlchemyImportSessionRepository(SqlAlchemyRepository[ImportSession]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, ImportSession)


class SqlAlchemyAuditRepository(SqlAlchemyRepository[AuditEvent]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, AuditEvent)


class SqlAlchemyImportErrorRepository(SqlAlchemyRepository[ImportError]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, ImportError)
