from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.core.entities import required_text
from app.core.identifiers import new_id
from app.core.time import require_utc, utc_now


class FinancialReviewType(StrEnum):
    OWN_ACCOUNT_FIAT_WITHDRAWAL = "own_account_fiat_withdrawal"
    DELISTING_LIQUIDATION = "delisting_liquidation"


class FinancialSuggestionType(StrEnum):
    OWN_ACCOUNT_FIAT_WITHDRAWAL = "own_account_fiat_withdrawal"
    POSSIBLE_DELISTING_LIQUIDATION = "possible_delisting_liquidation"


class SuggestionStatus(StrEnum):
    SUGGESTED = "suggested"
    REJECTED = "rejected"


class ResolutionStatus(StrEnum):
    CONFIRMED = "confirmed"


class ReviewConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TaxMappingStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"


@dataclass(kw_only=True)
class FinancialReviewSuggestion:
    transformation_issue_id: UUID
    suggestion_type: FinancialSuggestionType
    status: SuggestionStatus
    confidence: ReviewConfidence
    reasons: list[str]
    metadata: dict[str, Any]
    id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_reason: str | None = None

    def __post_init__(self) -> None:
        self.created_at = require_utc(self.created_at)
        self.reasons = [required_text(value, "reason") for value in self.reasons]
        if not self.reasons:
            raise ValueError("A financial review suggestion needs reasons.")
        self.metadata = dict(self.metadata)
        if self.decided_at is not None:
            self.decided_at = require_utc(self.decided_at)
        if self.decided_by is not None:
            self.decided_by = required_text(self.decided_by, "decided_by")
        if self.decision_reason is not None:
            self.decision_reason = required_text(
                self.decision_reason, "decision_reason"
            )


@dataclass(kw_only=True)
class FinancialReviewResolution:
    transformation_issue_id: UUID
    resolution_type: FinancialReviewType
    status: ResolutionStatus
    decided_at: datetime
    decided_by: str
    reason: str
    source: str
    confidence: ReviewConfidence | None
    tax_mapping_status: TaxMappingStatus
    metadata: dict[str, Any]
    id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.created_at = require_utc(self.created_at)
        self.decided_at = require_utc(self.decided_at)
        self.decided_by = required_text(self.decided_by, "decided_by")
        self.reason = required_text(self.reason, "reason")
        self.source = required_text(self.source, "source")
        self.metadata = dict(self.metadata)


@dataclass(kw_only=True)
class FinancialReviewRecordLink:
    raw_import_record_id: UUID
    role: str
    suggestion_id: UUID | None = None
    resolution_id: UUID | None = None
    id: UUID = field(default_factory=new_id)

    def __post_init__(self) -> None:
        self.role = required_text(self.role, "role")
        if (self.suggestion_id is None) == (self.resolution_id is None):
            raise ValueError("A review link must have exactly one parent.")
