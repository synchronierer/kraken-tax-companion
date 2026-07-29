from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.core.identifiers import new_id
from app.core.time import require_utc, utc_now


class ImportStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AuditActorType(StrEnum):
    USER = "user"
    SYSTEM = "system"


def positive_decimal(value: Decimal, field_name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be a Decimal.")
    if value <= Decimal("0"):
        raise ValueError(f"{field_name} must be greater than zero.")
    return value


def required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty.")
    return normalized


@dataclass(kw_only=True)
class ImportSession:
    source: str
    version: str
    status: ImportStatus
    started_at: datetime
    id: UUID = field(default_factory=new_id)
    ended_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.source = required_text(self.source, "source")
        self.version = required_text(self.version, "version")
        self.started_at = require_utc(self.started_at)
        if self.ended_at is not None:
            self.ended_at = require_utc(self.ended_at)
            if self.ended_at < self.started_at:
                raise ValueError("ended_at must not be earlier than started_at.")
        self.created_at = require_utc(self.created_at)
        self.updated_at = require_utc(self.updated_at)


@dataclass(kw_only=True)
class EarnLot:
    lot_id: UUID
    coin: str
    quantity: Decimal
    occurred_at: datetime
    import_session_id: UUID
    id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.coin = required_text(self.coin, "coin").upper()
        self.quantity = positive_decimal(self.quantity, "quantity")
        self.occurred_at = require_utc(self.occurred_at)
        self.created_at = require_utc(self.created_at)
        self.updated_at = require_utc(self.updated_at)


@dataclass(kw_only=True)
class Sale:
    coin: str
    quantity: Decimal
    occurred_at: datetime
    import_session_id: UUID
    id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.coin = required_text(self.coin, "coin").upper()
        self.quantity = positive_decimal(self.quantity, "quantity")
        self.occurred_at = require_utc(self.occurred_at)
        self.created_at = require_utc(self.created_at)
        self.updated_at = require_utc(self.updated_at)


@dataclass(kw_only=True)
class AuditEvent:
    occurred_at: datetime
    event_type: str
    entity_type: str
    entity_id: UUID
    actor_type: AuditActorType
    actor_id: str
    metadata: dict[str, Any]
    id: UUID = field(default_factory=new_id)

    def __post_init__(self) -> None:
        self.occurred_at = require_utc(self.occurred_at)
        self.event_type = required_text(self.event_type, "event_type")
        self.entity_type = required_text(self.entity_type, "entity_type")
        self.actor_id = required_text(self.actor_id, "actor_id")
        self.metadata = dict(self.metadata)


@dataclass(kw_only=True)
class PriceSnapshot:
    coin: str
    captured_at: datetime
    price_eur: Decimal
    source: str
    id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.coin = required_text(self.coin, "coin").upper()
        self.captured_at = require_utc(self.captured_at)
        self.price_eur = positive_decimal(self.price_eur, "price_eur")
        self.source = required_text(self.source, "source")
        self.created_at = require_utc(self.created_at)


@dataclass(kw_only=True)
class Configuration:
    id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.created_at = require_utc(self.created_at)
        self.updated_at = require_utc(self.updated_at)


@dataclass(kw_only=True)
class RawImportRecord:
    import_session_id: UUID
    source: str
    content_hash: str
    payload: dict[str, Any]
    id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.source = required_text(self.source, "source")
        self.content_hash = required_text(self.content_hash, "content_hash")
        self.payload = dict(self.payload)
        self.created_at = require_utc(self.created_at)
