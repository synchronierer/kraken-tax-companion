from types import TracebackType
from typing import Protocol, Self

from app.core.repositories import (
    AuditEventRepository,
    EarnLotRepository,
    ImportSessionRepository,
    RawImportRecordRepository,
    SaleRepository,
)


class UnitOfWork(Protocol):
    """Atomic application transaction boundary."""

    earn_lots: EarnLotRepository
    sales: SaleRepository
    import_sessions: ImportSessionRepository
    raw_import_records: RawImportRecordRepository
    audit_events: AuditEventRepository

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
