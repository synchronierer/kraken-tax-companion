from types import TracebackType
from typing import Protocol, Self

from app.core.repositories import (
    AcquisitionRepository,
    AuditRepository,
    DisposalRepository,
    DomainProvenanceRepository,
    EarnLotRepository,
    FeeEventRepository,
    ImportErrorRepository,
    ImportSessionRepository,
    RawImportRepository,
    SaleRepository,
    TradeExecutionRepository,
    TransformationDecisionRepository,
    TransformationIssueRepository,
    TransformationRunRepository,
    TransformationRunSessionRepository,
    ValuationRequirementRepository,
)


class UnitOfWork(Protocol):
    """Atomic application transaction boundary."""

    earn_lots: EarnLotRepository
    sales: SaleRepository
    import_sessions: ImportSessionRepository
    raw_imports: RawImportRepository
    audit: AuditRepository
    import_errors: ImportErrorRepository
    transformation_runs: TransformationRunRepository
    transformation_run_sessions: TransformationRunSessionRepository
    transformation_decisions: TransformationDecisionRepository
    transformation_issues: TransformationIssueRepository
    acquisitions: AcquisitionRepository
    disposals: DisposalRepository
    trade_executions: TradeExecutionRepository
    fee_events: FeeEventRepository
    domain_provenance: DomainProvenanceRepository
    valuation_requirements: ValuationRequirementRepository

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def flush(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
