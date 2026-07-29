"""Add batch identity and ordered raw record metadata.

Revision ID: 0003_import_batch_model
Revises: 0002_generic_import_engine
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_import_batch_model"
down_revision: str | None = "0002_generic_import_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("import_sessions") as batch:
        batch.add_column(sa.Column("import_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("error_summary", sa.String(1024), nullable=True))
        batch.create_check_constraint(
            "ck_import_sessions_hash_length",
            "import_hash IS NULL OR length(import_hash) = 64",
        )
    with op.batch_alter_table("raw_import_records") as batch:
        batch.drop_constraint("uq_raw_import_source_hash", type_="unique")
        batch.add_column(
            sa.Column(
                "sequence_number", sa.Integer(), nullable=False, server_default="0"
            )
        )
        batch.add_column(sa.Column("external_id", sa.String(255), nullable=True))
        batch.add_column(
            sa.Column(
                "technical_metadata",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch.create_unique_constraint(
            "uq_raw_import_session_sequence",
            ["import_session_id", "sequence_number"],
        )


def downgrade() -> None:
    with op.batch_alter_table("raw_import_records") as batch:
        batch.drop_constraint("uq_raw_import_session_sequence", type_="unique")
        batch.drop_column("technical_metadata")
        batch.drop_column("external_id")
        batch.drop_column("sequence_number")
        batch.create_unique_constraint(
            "uq_raw_import_source_hash", ["source", "content_hash"]
        )
    with op.batch_alter_table("import_sessions") as batch:
        batch.drop_constraint("ck_import_sessions_hash_length", type_="check")
        batch.drop_column("error_summary")
        batch.drop_column("import_hash")
