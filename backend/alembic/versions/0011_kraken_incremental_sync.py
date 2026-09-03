"""Add robust incremental Kraken sync runs.

Revision ID: 0011_kraken_incremental_sync
Revises: 0010_export_format_version
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.database.types import UtcDateTime

revision: str = "0011_kraken_incremental_sync"
down_revision: str | None = "0010_export_format_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid = sa.Uuid()
    timestamp = UtcDateTime()
    status = sa.Enum(
        "PROCESSING", "COMPLETED", "FAILED", name="krakensyncstatus", native_enum=False
    )
    op.create_table(
        "kraken_sync_runs",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("account_scope", sa.String(64), nullable=False),
        sa.Column("sync_kind", sa.String(32), nullable=False),
        sa.Column("status", status, nullable=False),
        sa.Column("requested_from", timestamp, nullable=False),
        sa.Column("requested_to", timestamp, nullable=False),
        sa.Column("lookback_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "previous_success_id",
            uuid,
            sa.ForeignKey("kraken_sync_runs.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "import_session_id",
            uuid,
            sa.ForeignKey("import_sessions.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "transformation_run_id",
            uuid,
            sa.ForeignKey("transformation_runs.id", ondelete="RESTRICT"),
        ),
        sa.Column("started_at", timestamp, nullable=False),
        sa.Column("ended_at", timestamp),
        sa.Column("fetched_pages", sa.Integer(), nullable=False),
        sa.Column("provider_records", sa.Integer(), nullable=False),
        sa.Column("unique_records", sa.Integer(), nullable=False),
        sa.Column("known_records", sa.Integer(), nullable=False),
        sa.Column("new_raw_records", sa.Integer(), nullable=False),
        sa.Column("new_domain_objects", sa.Integer(), nullable=False),
        sa.Column("review_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("ledger_id_digest", sa.String(64)),
        sa.Column("error_code", sa.String(128)),
        sa.Column("error_summary", sa.String(1024)),
        sa.Column("created_at", timestamp, nullable=False),
        sa.CheckConstraint(
            "requested_to > requested_from AND lookback_seconds > 0",
            name="ck_kraken_sync_window",
        ),
        sa.CheckConstraint(
            "fetched_pages >= 0 AND provider_records >= 0 AND unique_records >= 0 "
            "AND known_records >= 0 AND new_raw_records >= 0 "
            "AND new_domain_objects >= 0 AND review_count >= 0 "
            "AND error_count >= 0",
            name="ck_kraken_sync_counts",
        ),
        sa.CheckConstraint(
            "known_records + new_raw_records = unique_records",
            name="ck_kraken_sync_classification",
        ),
        sa.CheckConstraint(
            "ledger_id_digest IS NULL OR length(ledger_id_digest) = 64",
            name="ck_kraken_sync_digest",
        ),
        sa.CheckConstraint(
            "(status = 'PROCESSING' AND ended_at IS NULL AND error_code IS NULL) OR "
            "(status = 'COMPLETED' AND ended_at IS NOT NULL "
            "AND ledger_id_digest IS NOT NULL AND error_code IS NULL) OR "
            "(status = 'FAILED' AND ended_at IS NOT NULL AND error_code IS NOT NULL)",
            name="ck_kraken_sync_status",
        ),
    )
    op.create_index(
        "ix_kraken_sync_checkpoint",
        "kraken_sync_runs",
        ["account_scope", "sync_kind", "status", "requested_to"],
    )
    op.create_index(
        "uq_kraken_sync_active",
        "kraken_sync_runs",
        ["account_scope", "sync_kind"],
        unique=True,
        sqlite_where=sa.text("status = 'PROCESSING'"),
        postgresql_where=sa.text("status = 'PROCESSING'"),
    )


def downgrade() -> None:
    op.drop_index("uq_kraken_sync_active", table_name="kraken_sync_runs")
    op.drop_index("ix_kraken_sync_checkpoint", table_name="kraken_sync_runs")
    op.drop_table("kraken_sync_runs")
