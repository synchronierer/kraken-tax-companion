from types import TracebackType
from typing import Protocol, Self

from app.core.repositories import (
    AuditRepository,
    EarnLotRepository,
    ImportErrorRepository,
    ImportSessionRepository,
    RawImportRepository,
    SaleRepository,
)


class UnitOfWork(Protocol):
    """Atomic application transaction boundary."""

    earn_lots: EarnLotRepository
    sales: SaleRepository
    import_sessions: ImportSessionRepository
    raw_imports: RawImportRepository
    audit: AuditRepository
    import_errors: ImportErrorRepository

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
