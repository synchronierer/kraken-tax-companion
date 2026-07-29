from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.core.entities import AuditActorType, ImportSession
from app.core.time import require_utc


@dataclass(frozen=True, kw_only=True)
class ImportContext:
    session: ImportSession
    source: str
    version: str
    received_at: datetime
    actor_type: AuditActorType
    actor_id: str
    correlation_id: UUID

    def __post_init__(self) -> None:
        object.__setattr__(self, "received_at", require_utc(self.received_at))
        comparisons = (
            (self.source, self.session.source, "source"),
            (self.version, self.session.version, "version"),
            (self.actor_type, self.session.actor_type, "actor type"),
            (self.actor_id, self.session.actor_id, "actor ID"),
            (self.correlation_id, self.session.correlation_id, "correlation ID"),
        )
        for context_value, session_value, field_name in comparisons:
            if context_value != session_value:
                raise ValueError(f"Context {field_name} must match its session.")
