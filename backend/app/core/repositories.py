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
from app.core.transformation import (
    AcquisitionLot,
    DisposalEvent,
    DomainProvenance,
    FeeEvent,
    TradeExecution,
    TransformationDecision,
    TransformationIssue,
    TransformationRun,
    TransformationRunSession,
    ValuationRequirement,
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

    def list_by_import_sessions(
        self, import_session_ids: Sequence[UUID]
    ) -> Sequence[RawImportRecord]: ...

    def list_by_external_id(self, external_id: str) -> Sequence[RawImportRecord]: ...

    def find_by_canonical_key(self, canonical_key: str) -> RawImportRecord | None: ...


class AuditRepository(Repository[AuditEvent], Protocol):
    pass


class ImportErrorRepository(Repository[ImportError], Protocol):
    pass


RawImportRecordRepository = RawImportRepository
AuditEventRepository = AuditRepository


class StableProjectionRepository[EntityT](Repository[EntityT], Protocol):
    def find_by_stable_key(self, stable_key: str) -> EntityT | None: ...


class AcquisitionRepository(StableProjectionRepository[AcquisitionLot], Protocol):
    pass


class DisposalRepository(StableProjectionRepository[DisposalEvent], Protocol):
    pass


class TradeExecutionRepository(StableProjectionRepository[TradeExecution], Protocol):
    pass


class FeeEventRepository(StableProjectionRepository[FeeEvent], Protocol):
    pass


class TransformationRunRepository(Repository[TransformationRun], Protocol):
    pass


class TransformationDecisionRepository(Repository[TransformationDecision], Protocol):
    pass


class TransformationIssueRepository(Repository[TransformationIssue], Protocol):
    pass


class TransformationRunSessionRepository(
    Repository[TransformationRunSession], Protocol
):
    pass


class DomainProvenanceRepository(Repository[DomainProvenance], Protocol):
    pass


class ValuationRequirementRepository(Repository[ValuationRequirement], Protocol):
    pass
