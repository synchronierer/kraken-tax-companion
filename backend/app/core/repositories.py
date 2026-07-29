from collections.abc import Sequence
from typing import Protocol, TypeVar
from uuid import UUID

from app.core.entities import AuditEvent, EarnLot, ImportSession, RawImportRecord, Sale

EntityT = TypeVar("EntityT")


class Repository(Protocol[EntityT]):
    """Persistence-independent collection interface."""

    def add(self, entity: EntityT) -> None: ...

    def get(self, entity_id: UUID) -> EntityT | None: ...

    def list(self) -> Sequence[EntityT]: ...


class EarnLotRepository(Repository[EarnLot], Protocol):
    pass


class SaleRepository(Repository[Sale], Protocol):
    pass


class ImportSessionRepository(Repository[ImportSession], Protocol):
    pass


class RawImportRecordRepository(Repository[RawImportRecord], Protocol):
    pass


class AuditEventRepository(Repository[AuditEvent], Protocol):
    pass
