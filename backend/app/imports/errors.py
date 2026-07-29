from typing import Any

from app.core.entities import ErrorCategory


class ImportEngineError(Exception):
    def __init__(
        self,
        *,
        code: str,
        description: str,
        affected_record: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(description)
        self.code = code
        self.description = description
        self.affected_record = affected_record
        self.category = ErrorCategory.IMPORT


class ImportValidationError(ImportEngineError):
    pass


class ImportIntegrityError(ImportEngineError):
    pass


class DomainValidationError(Exception):
    """Reserved boundary for future domain validation failures."""
