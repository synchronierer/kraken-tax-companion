from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import DateTime, create_engine
from sqlalchemy.dialects import sqlite
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.orm import Session

from app import models
from app.database.base import Base
from app.database.mappings import (
    audit_events,
    import_errors,
    provider_evidence,
    raw_import_records,
)
from app.database.session import get_session
from app.database.types import (
    STRUCTURED_JSON,
    UtcDateTime,
    compare_database_type,
)


class ComparisonContext:
    def __init__(self, dialect: Dialect) -> None:
        self.dialect = dialect


def test_metadata_contains_domain_tables() -> None:
    models.configure_mappings()
    assert len(Base.metadata.tables) == 22


def test_session_dependency_provides_session() -> None:
    dependency = get_session()

    session = next(dependency)

    assert isinstance(session, Session)

    try:
        next(dependency)
    except StopIteration:
        pass
    else:
        raise AssertionError("Session dependency must yield exactly once.")


def test_utc_datetime_dialect_and_comparison_contract() -> None:
    utc_type = UtcDateTime()
    sqlite_dialect = sqlite.dialect()
    postgres_dialect = create_engine(
        "postgresql+psycopg://synthetic:synthetic@localhost/synthetic"
    ).dialect
    aware = datetime(2026, 7, 31, 14, tzinfo=timezone(timedelta(hours=2)))

    sqlite_value = utc_type.process_bind_param(aware, sqlite_dialect)
    postgres_value = utc_type.process_bind_param(aware, postgres_dialect)
    assert sqlite_value == datetime(2026, 7, 31, 12)
    assert postgres_value == datetime(2026, 7, 31, 12, tzinfo=UTC)
    assert utc_type.process_result_value(sqlite_value, sqlite_dialect) == datetime(
        2026, 7, 31, 12, tzinfo=UTC
    )
    assert utc_type.process_result_value(postgres_value, postgres_dialect) == datetime(
        2026, 7, 31, 12, tzinfo=UTC
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        utc_type.process_bind_param(datetime(2026, 7, 31, 12), sqlite_dialect)

    assert "WITH TIME ZONE" in str(utc_type.compile(dialect=postgres_dialect)).upper()
    postgres_context = ComparisonContext(postgres_dialect)
    assert (
        compare_database_type(
            postgres_context,
            object(),
            object(),
            DateTime(timezone=True),
            utc_type,
        )
        is False
    )
    assert (
        compare_database_type(
            postgres_context,
            object(),
            object(),
            DateTime(timezone=False),
            utc_type,
        )
        is True
    )
    assert (
        compare_database_type(
            ComparisonContext(sqlite_dialect),
            object(),
            object(),
            DateTime(),
            utc_type,
        )
        is False
    )
    assert (
        compare_database_type(
            postgres_context,
            object(),
            object(),
            DateTime(timezone=True),
            DateTime(timezone=True),
        )
        is None
    )
    other_dialect = sqlite.dialect()
    other_dialect.name = "other"
    assert (
        compare_database_type(
            ComparisonContext(other_dialect),
            object(),
            object(),
            DateTime(),
            utc_type,
        )
        is None
    )


def test_structured_json_uses_jsonb_only_on_postgresql() -> None:
    postgres_dialect = create_engine(
        "postgresql+psycopg://synthetic:synthetic@localhost/synthetic"
    ).dialect
    sqlite_dialect = sqlite.dialect()
    assert str(STRUCTURED_JSON.compile(dialect=postgres_dialect)) == "JSONB"
    assert str(STRUCTURED_JSON.compile(dialect=sqlite_dialect)) == "JSON"
    for table, column_name in (
        (audit_events, "metadata"),
        (import_errors, "affected_record"),
        (raw_import_records, "payload"),
        (raw_import_records, "technical_metadata"),
        (provider_evidence, "observations"),
    ):
        column_type = table.c[column_name].type
        assert str(column_type.compile(dialect=postgres_dialect)) == "JSONB"
        assert str(column_type.compile(dialect=sqlite_dialect)) == "JSON"
