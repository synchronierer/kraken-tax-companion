from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from app.core.entities import required_text
from app.core.identifiers import new_id
from app.core.time import require_utc, utc_now
from app.core.transformation import non_negative_decimal


class HistoricalTransferNature(StrEnum):
    SELF_TRANSFER = "self_transfer"
    EXTERNAL_RECEIPT = "external_receipt"
    UNKNOWN = "unknown"


class HistoricalBasisCoverage(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class HistoricalResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    PARTIAL = "partial"
    REVIEW_REQUIRED = "review_required"


@dataclass(kw_only=True)
class HistoricalTransferLink:
    stable_key: str
    version: str
    evidence_type: str
    source_record_ids: tuple[str, ...]
    canonical_asset: str
    source_quantity: Decimal
    target_quantity: Decimal
    transfer_nature: HistoricalTransferNature
    basis_coverage: HistoricalBasisCoverage
    source_fee_quantity: Decimal
    source_fee_asset: str | None
    transport_difference: Decimal
    evidence_hash: str
    resolution_hash: str
    target_raw_import_record_id: UUID | None = None
    target_fingerprint: str | None = None
    parent_link_id: UUID | None = None
    created_at: datetime = field(default_factory=utc_now)
    created_by: str = "historical-reconciliation"
    id: UUID = field(default_factory=new_id)

    def __post_init__(self) -> None:
        self.stable_key = required_text(self.stable_key, "stable_key")
        self.version = required_text(self.version, "version")
        self.evidence_type = required_text(self.evidence_type, "evidence_type")
        self.source_record_ids = tuple(
            required_text(item, "source_record_id") for item in self.source_record_ids
        )
        self.canonical_asset = required_text(self.canonical_asset, "canonical_asset")
        for field_name in ("source_quantity", "target_quantity"):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{field_name} must be finite.")
            setattr(self, field_name, non_negative_decimal(value, field_name))
        self.source_fee_quantity = non_negative_decimal(
            self.source_fee_quantity, "source_fee_quantity"
        )
        self.transport_difference = non_negative_decimal(
            self.transport_difference, "transport_difference"
        )
        self.evidence_hash = required_text(self.evidence_hash, "evidence_hash")
        self.resolution_hash = required_text(self.resolution_hash, "resolution_hash")
        self.created_by = required_text(self.created_by, "created_by")
        self.created_at = require_utc(self.created_at)


@dataclass(kw_only=True)
class HistoricalTransferResolution:
    stable_key: str
    historical_transfer_link_id: UUID
    version: str
    status: HistoricalResolutionStatus
    basis_coverage: HistoricalBasisCoverage
    evidence_level: str
    resolution_hash: str
    explanation: str
    created_at: datetime = field(default_factory=utc_now)
    created_by: str = "historical-reconciliation"
    resolved_at: datetime | None = None
    id: UUID = field(default_factory=new_id)

    def __post_init__(self) -> None:
        self.stable_key = required_text(self.stable_key, "stable_key")
        self.version = required_text(self.version, "version")
        self.evidence_level = required_text(self.evidence_level, "evidence_level")
        self.resolution_hash = required_text(self.resolution_hash, "resolution_hash")
        self.explanation = required_text(self.explanation, "explanation")
        self.created_by = required_text(self.created_by, "created_by")
        self.created_at = require_utc(self.created_at)
        if self.resolved_at is not None:
            self.resolved_at = require_utc(self.resolved_at)
