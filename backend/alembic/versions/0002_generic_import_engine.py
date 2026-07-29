"""Add generic import engine persistence.

Revision ID: 0002_generic_import_engine
Revises: 0001_domain_foundation
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_generic_import_engine"
down_revision: str | None = "0001_domain_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

uuid_type = sa.Uuid(as_uuid=True)
timestamp_type = sa.DateTime(timezone=True)
json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
status_type = sa.Enum(
    "CREATED",
    "RECEIVED",
    "VALIDATING",
    "HASHING",
    "CHECKING_DUPLICATES",
    "PERSISTING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    native_enum=False,
)
actor_type = sa.Enum("USER", "SYSTEM", native_enum=False)
error_category = sa.Enum("IMPORT", "DOMAIN", native_enum=False)


def upgrade() -> None:
    with op.batch_alter_table("import_sessions") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.String(9),
            type_=status_type,
            existing_nullable=False,
        )
        batch.add_column(sa.Column("correlation_id", uuid_type, nullable=False))
        batch.add_column(sa.Column("actor_type", actor_type, nullable=False))
        batch.add_column(sa.Column("actor_id", sa.String(255), nullable=False))
        batch.add_column(sa.Column("received_count", sa.Integer(), nullable=False))
        batch.add_column(sa.Column("persisted_count", sa.Integer(), nullable=False))
        batch.add_column(sa.Column("skipped_count", sa.Integer(), nullable=False))
        batch.create_unique_constraint(
            "uq_import_sessions_correlation_id", ["correlation_id"]
        )
    with op.batch_alter_table("raw_import_records") as batch:
        batch.create_unique_constraint(
            "uq_raw_import_source_hash", ["source", "content_hash"]
        )
    op.create_table(
        "import_errors",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "import_session_id",
            uuid_type,
            sa.ForeignKey("import_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("occurred_at", timestamp_type, nullable=False),
        sa.Column("category", error_category, nullable=False),
        sa.Column("error_code", sa.String(128), nullable=False),
        sa.Column("description", sa.String(1024), nullable=False),
        sa.Column("original_exception", sa.String(2048), nullable=False),
        sa.Column("affected_record", json_type, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("import_errors")
    with op.batch_alter_table("raw_import_records") as batch:
        batch.drop_constraint("uq_raw_import_source_hash", type_="unique")
    with op.batch_alter_table("import_sessions") as batch:
        batch.drop_constraint("uq_import_sessions_correlation_id", type_="unique")
        for column_name in (
            "skipped_count",
            "persisted_count",
            "received_count",
            "actor_id",
            "actor_type",
            "correlation_id",
        ):
            batch.drop_column(column_name)
        batch.alter_column(
            "status",
            existing_type=status_type,
            type_=sa.String(9),
            existing_nullable=False,
        )
