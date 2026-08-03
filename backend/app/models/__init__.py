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
from app.core.tax import (
    DisposalCalculation,
    ExportArtifact,
    ExportRun,
    InventoryLot,
    LotAllocation,
    TaxCalculationRun,
    TaxJournalEntry,
    TaxReviewCase,
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
    "ExportArtifact",
    "ExportRun",
    "DisposalEvent",
    "DomainProvenance",
    "FeeEvent",
    "ImportError",
    "ImportSession",
    "ImportStatus",
    "InventoryLot",
    "LotAllocation",
    "PriceSnapshot",
    "RawImportRecord",
    "Sale",
    "TradeExecution",
    "TaxCalculationRun",
    "TaxJournalEntry",
    "TaxReviewCase",
    "DisposalCalculation",
    "TransformationDecision",
    "TransformationIssue",
    "TransformationRun",
    "ValuationRequirement",
    "configure_mappings",
]
