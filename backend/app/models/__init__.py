"""Shared persistence model registry."""

from app.core.entities import (
    AuditActorType,
    AuditEvent,
    Configuration,
    EarnLot,
    ImportError,
    ImportSession,
    ImportStatus,
    PriceSnapshot,
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
    ValuationRequirement,
)
from app.database.mappings import configure_mappings

configure_mappings()

__all__ = [
    "AuditActorType",
    "AuditEvent",
    "AcquisitionLot",
    "Configuration",
    "EarnLot",
    "DisposalEvent",
    "DomainProvenance",
    "FeeEvent",
    "ImportError",
    "ImportSession",
    "ImportStatus",
    "PriceSnapshot",
    "RawImportRecord",
    "Sale",
    "TradeExecution",
    "TransformationDecision",
    "TransformationIssue",
    "TransformationRun",
    "ValuationRequirement",
    "configure_mappings",
]
