from pathlib import Path
from uuid import uuid4

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.config.settings import get_settings


def test_domain_migration_up_and_down(tmp_path: Path, monkeypatch: object) -> None:
    database_path = tmp_path / "migration.db"
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setenv("APP_DATABASE_URL", database_url)  # type: ignore[attr-defined]
    get_settings.cache_clear()
    config = Config("alembic.ini")

    command.upgrade(config, "0003_import_batch_model")
    engine = create_engine(database_url)
    session_id = uuid4().hex
    record_id = uuid4().hex
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO import_sessions "
                "(id, source, version, status, started_at, correlation_id, "
                "actor_type, actor_id, received_count, persisted_count, "
                "skipped_count, created_at, updated_at, import_hash) "
                "VALUES (:id, 'synthetic', '1', 'COMPLETED', :now, :correlation, "
                "'SYSTEM', 'migration-test', 1, 1, 0, :now, :now, :hash)"
            ),
            {
                "id": session_id,
                "now": "2026-07-30 12:00:00",
                "correlation": uuid4().hex,
                "hash": "a" * 64,
            },
        )
        connection.execute(
            text(
                "INSERT INTO raw_import_records "
                "(id, import_session_id, source, content_hash, payload, created_at, "
                "sequence_number, technical_metadata) "
                "VALUES (:id, :session, 'synthetic', :hash, '{}', :now, 0, '{}')"
            ),
            {
                "id": record_id,
                "session": session_id,
                "hash": "b" * 64,
                "now": "2026-07-30 12:00:00",
            },
        )
    command.upgrade(config, "head")
    command.check(config)
    inspector = inspect(engine)
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
        "transformation_runs",
        "transformation_run_sessions",
        "transformation_decisions",
        "transformation_issues",
        "trade_executions",
        "acquisition_lots",
        "disposal_events",
        "fee_events",
        "domain_provenance",
        "valuation_requirements",
        "valuation_runs",
        "daily_prices",
        "valuation_decisions",
        "provider_evidence",
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
    assert any(
        constraint["column_names"] == ["stable_key"]
        for constraint in inspector.get_unique_constraints("trade_executions")
    )
    assert {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("daily_prices")
    } == {"uq_daily_price_evidence", "uq_daily_price_version"}
    assert {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("provider_evidence")
    } == {"uq_provider_evidence_identity"}
    assert {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("valuation_decisions")
    } == {"uq_valuation_decision_version"}
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM raw_import_records")) == 1

    command.downgrade(config, "base")
    assert inspect(create_engine(database_url)).get_table_names() == ["alembic_version"]
    command.upgrade(config, "head")
    command.check(config)
    assert "transformation_runs" in inspect(engine).get_table_names()
    get_settings.cache_clear()
