"""Create domain foundation tables.

Revision ID: 0001_domain_foundation
Revises:
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_domain_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

uuid_type = sa.Uuid(as_uuid=True)
amount_type = sa.Numeric(38, 18).with_variant(sa.String(80), "sqlite")
timestamp_type = sa.DateTime(timezone=True)
json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def timestamps(*, updated: bool = True) -> list[sa.Column[object]]:
    columns: list[sa.Column[object]] = [
        sa.Column("created_at", timestamp_type, nullable=False)
    ]
    if updated:
        columns.append(sa.Column("updated_at", timestamp_type, nullable=False))
    return columns


def upgrade() -> None:
    op.create_table(
        "import_sessions",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column(
            "status",
            sa.Enum("PENDING", "RUNNING", "COMPLETED", "FAILED", native_enum=False),
            nullable=False,
        ),
        sa.Column("started_at", timestamp_type, nullable=False),
        sa.Column("ended_at", timestamp_type, nullable=True),
        *timestamps(),
    )
    op.create_table(
        "earn_lots",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("lot_id", uuid_type, nullable=False, unique=True),
        sa.Column("coin", sa.String(32), nullable=False),
        sa.Column("quantity", amount_type, nullable=False),
        sa.Column("occurred_at", timestamp_type, nullable=False),
        sa.Column(
            "import_session_id",
            uuid_type,
            sa.ForeignKey("import_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        *timestamps(),
        sa.CheckConstraint("quantity > 0", name="ck_earn_lots_quantity_positive"),
    )
    op.create_table(
        "sales",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("coin", sa.String(32), nullable=False),
        sa.Column("quantity", amount_type, nullable=False),
        sa.Column("occurred_at", timestamp_type, nullable=False),
        sa.Column(
            "import_session_id",
            uuid_type,
            sa.ForeignKey("import_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        *timestamps(),
        sa.CheckConstraint("quantity > 0", name="ck_sales_quantity_positive"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("occurred_at", timestamp_type, nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("entity_type", sa.String(128), nullable=False),
        sa.Column("entity_id", uuid_type, nullable=False),
        sa.Column(
            "actor_type", sa.Enum("USER", "SYSTEM", native_enum=False), nullable=False
        ),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("metadata", json_type, nullable=False),
    )
    op.create_table(
        "price_snapshots",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("coin", sa.String(32), nullable=False),
        sa.Column("captured_at", timestamp_type, nullable=False),
        sa.Column("price_eur", amount_type, nullable=False),
        sa.Column("source", sa.String(128), nullable=False),
        *timestamps(updated=False),
        sa.CheckConstraint("price_eur > 0", name="ck_price_snapshots_price_positive"),
    )
    op.create_table(
        "configurations",
        sa.Column("id", uuid_type, primary_key=True),
        *timestamps(),
    )
    op.create_table(
        "raw_import_records",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "import_session_id",
            uuid_type,
            sa.ForeignKey("import_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.Column("payload", json_type, nullable=False),
        *timestamps(updated=False),
    )


def downgrade() -> None:
    for table_name in (
        "raw_import_records",
        "configurations",
        "price_snapshots",
        "audit_events",
        "sales",
        "earn_lots",
        "import_sessions",
    ):
        op.drop_table(table_name)
