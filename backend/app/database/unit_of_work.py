from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from app.core.entities import EarnLot, Sale
from app.core.repositories import (
    AuditRepository,
    EarnLotRepository,
    ImportErrorRepository,
    ImportSessionRepository,
    RawImportRepository,
    SaleRepository,
)
from app.database.repositories import (
    SqlAlchemyAuditRepository,
    SqlAlchemyImportErrorRepository,
    SqlAlchemyImportSessionRepository,
    SqlAlchemyRawImportRepository,
    SqlAlchemyRepository,
)


class SqlAlchemyUnitOfWork:
    earn_lots: EarnLotRepository
    sales: SaleRepository
    import_sessions: ImportSessionRepository
    raw_imports: RawImportRepository
    audit: AuditRepository
    import_errors: ImportErrorRepository

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self.committed = False

    def __enter__(self) -> Self:
        self._session = self._session_factory()
        self.earn_lots = SqlAlchemyRepository(self._session, EarnLot)
        self.sales = SqlAlchemyRepository(self._session, Sale)
        self.import_sessions = SqlAlchemyImportSessionRepository(self._session)
        self.raw_imports = SqlAlchemyRawImportRepository(self._session)
        self.audit = SqlAlchemyAuditRepository(self._session)
        self.import_errors = SqlAlchemyImportErrorRepository(self._session)
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
        if exc_type is not None or not self.committed:
            self._session.rollback()
        self._session.close()
        self._session = None

    def flush(self) -> None:
        self._require_session().flush()

    def commit(self) -> None:
        self._require_session().commit()
        self.committed = True

    def rollback(self) -> None:
        self._require_session().rollback()

    def _require_session(self) -> Session:
        if self._session is None:
            raise RuntimeError("Unit of work must be entered before use.")
        return self._session
