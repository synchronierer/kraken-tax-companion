from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeDecorator, TypeEngine

from app.core.time import require_utc


class UtcDateTime(TypeDecorator[datetime]):
    """Persist UTC timestamps while restoring awareness on SQLite."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        del dialect
        if value is None:
            return None
        return require_utc(value).replace(tzinfo=None)

    def process_result_value(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        del dialect
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


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
