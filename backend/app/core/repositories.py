from collections.abc import Sequence
from typing import Protocol, TypeVar
from uuid import UUID

from app.core.entities import (
    AuditEvent,
    EarnLot,
    ImportError,
    ImportSession,
    RawImportRecord,
    Sale,
)

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
    def find_by_hash(
        self, source: str, import_hash: str, *, exclude_id: UUID | None = None
    ) -> ImportSession | None: ...


class RawImportRepository(Repository[RawImportRecord], Protocol):
    def find_by_hash(
        self, source: str, content_hash: str
    ) -> RawImportRecord | None: ...


class AuditRepository(Repository[AuditEvent], Protocol):
    pass


class ImportErrorRepository(Repository[ImportError], Protocol):
    pass


RawImportRecordRepository = RawImportRepository
AuditEventRepository = AuditRepository
