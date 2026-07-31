from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import JSON, DateTime, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeDecorator, TypeEngine

from app.core.time import require_utc

STRUCTURED_JSON = JSON().with_variant(JSONB(), "postgresql")


class UtcDateTime(TypeDecorator[datetime]):
    """Persist aware UTC timestamps with a dialect-appropriate SQL type."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        return dialect.type_descriptor(DateTime(timezone=dialect.name == "postgresql"))

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        normalized = require_utc(value)
        return (
            normalized.replace(tzinfo=None) if dialect.name == "sqlite" else normalized
        )

    def process_result_value(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class TypeComparisonContext(Protocol):
    dialect: Dialect


def compare_database_type(
    context: TypeComparisonContext,
    inspected_column: object,
    metadata_column: object,
    inspected_type: TypeEngine[Any],
    metadata_type: TypeEngine[Any],
) -> bool | None:
    """Treat only the physical representation of UtcDateTime as equivalent."""

    del inspected_column, metadata_column
    if not isinstance(metadata_type, UtcDateTime) or not isinstance(
        inspected_type, DateTime
    ):
        return None
    if context.dialect.name == "postgresql":
        return inspected_type.timezone is not True
    if context.dialect.name == "sqlite":
        return False
    return None


class ExactDecimal(TypeDecorator[Decimal]):
    """Store decimals losslessly in SQLite and numerically in PostgreSQL."""

    impl = Numeric
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(80))
        return dialect.type_descriptor(Numeric(38, 18))

    def process_bind_param(
        self, value: Decimal | None, dialect: Dialect
    ) -> str | Decimal | None:
        if value is None:
            return None
        if not isinstance(value, Decimal):
            raise TypeError("Database amount must be a Decimal.")
        if dialect.name == "sqlite":
            return format(value, "f")
        return value

    def process_result_value(
        self, value: object | None, dialect: Dialect
    ) -> Decimal | None:
        del dialect
        if value is None:
            return None
        return Decimal(str(value))
