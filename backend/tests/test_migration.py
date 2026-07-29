from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from app.config.settings import get_settings


def test_domain_migration_up_and_down(tmp_path: Path, monkeypatch: object) -> None:
    database_path = tmp_path / "migration.db"
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setenv("APP_DATABASE_URL", database_url)  # type: ignore[attr-defined]
    get_settings.cache_clear()
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    command.check(config)
    inspector = inspect(create_engine(database_url))
    tables = set(inspector.get_table_names())
    assert tables == {
        "alembic_version",
        "audit_events",
        "configurations",
        "earn_lots",
        "import_sessions",
        "import_errors",
        "price_snapshots",
        "raw_import_records",
        "sales",
    }
    assert {"import_hash", "error_summary"}.issubset(
        {column["name"] for column in inspector.get_columns("import_sessions")}
    )
    assert {"sequence_number", "external_id", "technical_metadata"}.issubset(
        {column["name"] for column in inspector.get_columns("raw_import_records")}
    )
    assert {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("raw_import_records")
    } == {"uq_raw_import_session_sequence"}

    command.downgrade(config, "base")
    assert inspect(create_engine(database_url)).get_table_names() == ["alembic_version"]
    get_settings.cache_clear()
