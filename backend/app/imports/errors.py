from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.core.entities import ErrorCategory


class IssueCategory(StrEnum):
    TECHNICAL = "technical"
    VALIDATION = "validation"
    TRANSFORMATION = "transformation"
    PERSISTENCE = "persistence"


class ImportEngineError(Exception):
    issue_category = IssueCategory.VALIDATION

    def __init__(
        self,
        *,
        code: str,
        description: str,
        affected_record: dict[str, Any] | None = None,
        record_position: int | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(description)
        self.code = code
        self.description = description
        self.affected_record = affected_record
        self.category = ErrorCategory.IMPORT
        self.record_position = record_position
        self.field = field

    def issue(self) -> "ValidationIssue":
        return ValidationIssue(
            code=self.code,
            message=self.description,
            category=self.issue_category,
            record_position=self.record_position,
            field=self.field,
        )


@dataclass(frozen=True, kw_only=True)
class ValidationIssue:
    code: str
    message: str
    category: IssueCategory
    record_position: int | None = None
    field: str | None = None


class ImportValidationError(ImportEngineError):
    pass


class ImportIntegrityError(ImportEngineError):
    pass


class TechnicalImportError(ImportEngineError):
    issue_category = IssueCategory.TECHNICAL


class PersistenceImportError(ImportEngineError):
    issue_category = IssueCategory.PERSISTENCE


class TransformationError(ImportEngineError):
    issue_category = IssueCategory.TRANSFORMATION

    def __init__(
        self,
        *,
        code: str,
        description: str,
        affected_record: dict[str, Any] | None = None,
        record_position: int | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(
            code=code,
            description=description,
            affected_record=affected_record,
            record_position=record_position,
            field=field,
        )
        self.category = ErrorCategory.DOMAIN


class DuplicateImportError(ImportEngineError):
    """Optional exception form; normal duplicate control flow uses ImportResult."""


class InvalidStateTransitionError(ImportEngineError):
    pass


class DomainValidationError(TransformationError):
    """Compatibility name for domain-near transformation failures."""
