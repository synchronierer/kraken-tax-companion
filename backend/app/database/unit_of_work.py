from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from app.core.entities import EarnLot, Sale
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
from app.database.repositories import (
    SqlAlchemyAuditRepository,
    SqlAlchemyImportErrorRepository,
    SqlAlchemyImportSessionRepository,
    SqlAlchemyRawImportRepository,
    SqlAlchemyRepository,
    SqlAlchemyStableProjectionRepository,
)


class SqlAlchemyUnitOfWork:
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

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        external_session: Session | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._external_session = external_session
        self._session: Session | None = None
        self.committed = False

    def __enter__(self) -> Self:
        self._session = self._external_session or self._session_factory()
        self.earn_lots = SqlAlchemyRepository(self._session, EarnLot)
        self.sales = SqlAlchemyRepository(self._session, Sale)
        self.import_sessions = SqlAlchemyImportSessionRepository(self._session)
        self.raw_imports = SqlAlchemyRawImportRepository(self._session)
        self.audit = SqlAlchemyAuditRepository(self._session)
        self.import_errors = SqlAlchemyImportErrorRepository(self._session)
        self.transformation_runs = SqlAlchemyRepository(
            self._session, TransformationRun
        )
        self.transformation_run_sessions = SqlAlchemyRepository(
            self._session, TransformationRunSession
        )
        self.transformation_decisions = SqlAlchemyRepository(
            self._session, TransformationDecision
        )
        self.transformation_issues = SqlAlchemyRepository(
            self._session, TransformationIssue
        )
        self.acquisitions = SqlAlchemyStableProjectionRepository(
            self._session, AcquisitionLot
        )
        self.disposals = SqlAlchemyStableProjectionRepository(
            self._session, DisposalEvent
        )
        self.trade_executions = SqlAlchemyStableProjectionRepository(
            self._session, TradeExecution
        )
        self.fee_events = SqlAlchemyStableProjectionRepository(self._session, FeeEvent)
        self.domain_provenance = SqlAlchemyRepository(self._session, DomainProvenance)
        self.valuation_requirements = SqlAlchemyRepository(
            self._session, ValuationRequirement
        )
        self.committed = False
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_value, traceback
        if self._session is None:
            return
        if self._external_session is not None:
            self._session = None
            return
        if exc_type is not None or not self.committed:
            self._session.rollback()
        self._session.close()
        self._session = None

    def flush(self) -> None:
        self._require_session().flush()

    def commit(self) -> None:
        if self._external_session is None:
            self._require_session().commit()
        else:
            self._require_session().flush()
        self.committed = True

    def rollback(self) -> None:
        self._require_session().rollback()

    def _require_session(self) -> Session:
        if self._session is None:
            raise RuntimeError("Unit of work must be entered before use.")
        return self._session
