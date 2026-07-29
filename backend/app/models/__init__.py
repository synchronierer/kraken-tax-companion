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
from app.database.mappings import configure_mappings

configure_mappings()

__all__ = [
    "AuditActorType",
    "AuditEvent",
    "Configuration",
    "EarnLot",
    "ImportError",
    "ImportSession",
    "ImportStatus",
    "PriceSnapshot",
    "RawImportRecord",
    "Sale",
    "configure_mappings",
]
