from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    Enum,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapper
from sqlalchemy.types import Uuid

from app.core.entities import (
    AuditActorType,
    AuditEvent,
    Configuration,
    EarnLot,
    ErrorCategory,
    ImportError,
    ImportSession,
    ImportStatus,
    PriceSnapshot,
    RawImportRecord,
    Sale,
)
from app.database.base import mapper_registry
from app.database.types import ExactDecimal, UtcDateTime

UUID = Uuid(as_uuid=True)
AMOUNT = ExactDecimal()
COIN = String(32)
SOURCE = String(128)

import_sessions = Table(
    "import_sessions",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column("source", SOURCE, nullable=False),
    Column("version", String(64), nullable=False),
    Column("status", Enum(ImportStatus, native_enum=False), nullable=False),
    Column("started_at", UtcDateTime(), nullable=False),
    Column("correlation_id", UUID, nullable=False, unique=True),
    Column("actor_type", Enum(AuditActorType, native_enum=False), nullable=False),
    Column("actor_id", String(255), nullable=False),
    Column("ended_at", UtcDateTime(), nullable=True),
    Column("received_count", Integer, nullable=False),
    Column("persisted_count", Integer, nullable=False),
    Column("skipped_count", Integer, nullable=False),
    Column("created_at", UtcDateTime(), nullable=False),
    Column("updated_at", UtcDateTime(), nullable=False),
    Column("import_hash", String(64), nullable=True),
    Column("error_summary", String(1024), nullable=True),
    CheckConstraint(
        "import_hash IS NULL OR length(import_hash) = 64",
        name="ck_import_sessions_hash_length",
    ),
)


def import_reference() -> Column[Any]:
    return Column(
        "import_session_id",
        UUID,
        ForeignKey("import_sessions.id", ondelete="RESTRICT"),
        nullable=False,
    )


earn_lots = Table(
    "earn_lots",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column("lot_id", UUID, nullable=False, unique=True),
    Column("coin", COIN, nullable=False),
    Column("quantity", AMOUNT, nullable=False),
    Column("occurred_at", UtcDateTime(), nullable=False),
    import_reference(),
    Column("created_at", UtcDateTime(), nullable=False),
    Column("updated_at", UtcDateTime(), nullable=False),
    CheckConstraint("quantity > 0", name="ck_earn_lots_quantity_positive"),
)

sales = Table(
    "sales",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column("coin", COIN, nullable=False),
    Column("quantity", AMOUNT, nullable=False),
    Column("occurred_at", UtcDateTime(), nullable=False),
    import_reference(),
    Column("created_at", UtcDateTime(), nullable=False),
    Column("updated_at", UtcDateTime(), nullable=False),
    CheckConstraint("quantity > 0", name="ck_sales_quantity_positive"),
)

audit_events = Table(
    "audit_events",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column("occurred_at", UtcDateTime(), nullable=False),
    Column("event_type", String(128), nullable=False),
    Column("entity_type", String(128), nullable=False),
    Column("entity_id", UUID, nullable=False),
    Column("actor_type", Enum(AuditActorType, native_enum=False), nullable=False),
    Column("actor_id", String(255), nullable=False),
    Column("metadata", JSON, nullable=False),
)

price_snapshots = Table(
    "price_snapshots",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column("coin", COIN, nullable=False),
    Column("captured_at", UtcDateTime(), nullable=False),
    Column("price_eur", AMOUNT, nullable=False),
    Column("source", SOURCE, nullable=False),
    Column("created_at", UtcDateTime(), nullable=False),
    CheckConstraint("price_eur > 0", name="ck_price_snapshots_price_positive"),
)

configurations = Table(
    "configurations",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column("created_at", UtcDateTime(), nullable=False),
    Column("updated_at", UtcDateTime(), nullable=False),
)

raw_import_records = Table(
    "raw_import_records",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    import_reference(),
    Column("source", SOURCE, nullable=False),
    Column("content_hash", String(128), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created_at", UtcDateTime(), nullable=False),
    Column("sequence_number", Integer, nullable=False),
    Column("external_id", String(255), nullable=True),
    Column("technical_metadata", JSON, nullable=False),
    UniqueConstraint(
        "import_session_id",
        "sequence_number",
        name="uq_raw_import_session_sequence",
    ),
)

import_errors = Table(
    "import_errors",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    import_reference(),
    Column("occurred_at", UtcDateTime(), nullable=False),
    Column("category", Enum(ErrorCategory, native_enum=False), nullable=False),
    Column("error_code", String(128), nullable=False),
    Column("description", String(1024), nullable=False),
    Column("original_exception", String(2048), nullable=False),
    Column("affected_record", JSON, nullable=True),
)


def reject_update(_: Mapper[Any], connection: Any, target: object) -> None:
    del connection, target
    raise ValueError("Immutable records cannot be updated.")


def configure_mappings() -> None:
    """Register entities with persistence mappings exactly once."""
    if list(mapper_registry.mappers):
        return
    immutable_mappers = [
        mapper_registry.map_imperatively(EarnLot, earn_lots),
        mapper_registry.map_imperatively(AuditEvent, audit_events),
        mapper_registry.map_imperatively(PriceSnapshot, price_snapshots),
        mapper_registry.map_imperatively(RawImportRecord, raw_import_records),
        mapper_registry.map_imperatively(ImportError, import_errors),
    ]
    mapper_registry.map_imperatively(ImportSession, import_sessions)
    mapper_registry.map_imperatively(Sale, sales)
    mapper_registry.map_imperatively(Configuration, configurations)
    for mapper in immutable_mappers:
        event.listen(mapper, "before_update", reject_update)
