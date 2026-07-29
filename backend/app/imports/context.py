from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any
from uuid import UUID

from app.core.entities import AuditActorType, ImportSession
from app.core.time import require_utc


@dataclass(frozen=True, kw_only=True)
class ImportContext:
    session: ImportSession = field(compare=False)
    source: str
    version: str
    received_at: datetime = field(compare=False)
    actor_type: AuditActorType = field(compare=False)
    actor_id: str = field(compare=False)
    correlation_id: UUID = field(compare=False)
    source_name: str | None = None
    metadata: Mapping[str, Any] = field(
        default_factory=dict, compare=False, hash=False, repr=False
    )
    identity_data: Mapping[str, str] = field(
        default_factory=dict, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "received_at", require_utc(self.received_at))
        if self.source_name is not None and not self.source_name.strip():
            raise ValueError("source_name must not be empty.")
        object.__setattr__(
            self,
            "source_name",
            self.source_name.strip() if self.source_name else self.source,
        )
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        object.__setattr__(
            self, "identity_data", MappingProxyType(dict(self.identity_data))
        )
        if any(
            not key.strip() or not value.strip()
            for key, value in self.identity_data.items()
        ):
            raise ValueError("identity_data keys and values must not be empty.")
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

    @property
    def identity(self) -> tuple[str, str, str | None, tuple[tuple[str, str], ...]]:
        """Return identity fields; timestamps and user metadata are descriptive."""

        return (
            self.source,
            self.version,
            self.source_name,
            tuple(sorted(self.identity_data.items())),
        )
