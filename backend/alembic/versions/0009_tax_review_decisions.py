"""Add immutable manual tax review decisions.

Revision ID: 0009_tax_review_decisions
Revises: 0008_reward_valuation_components
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_tax_review_decisions"
down_revision: str | None = "0008_reward_valuation_components"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

uuid = sa.Uuid(as_uuid=True)
ts = sa.DateTime(timezone=True)


def enum(*values: str) -> sa.Enum:
    return sa.Enum(*values, native_enum=False)


def upgrade() -> None:
    op.create_table(
        "tax_review_decisions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "valuation_decision_id",
            uuid,
            sa.ForeignKey("valuation_decisions.id"),
            nullable=False,
        ),
        sa.Column(
            "source_tax_review_case_id",
            uuid,
            sa.ForeignKey("tax_review_cases.id"),
            nullable=False,
        ),
        sa.Column(
            "decision",
            enum(
                "INCLUDE_AS_WERBUNGSKOSTEN",
                "EXCLUDE_FROM_WERBUNGSKOSTEN",
            ),
            nullable=False,
        ),
        sa.Column("reason", sa.String(1024), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("decided_at", ts, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", uuid, sa.ForeignKey("tax_review_decisions.id")),
        sa.Column("batch_id", uuid, nullable=False),
        sa.UniqueConstraint(
            "valuation_decision_id",
            "version",
            name="uq_tax_review_decision_version",
        ),
        sa.CheckConstraint(
            "version > 0 AND length(reason) > 0 AND length(actor_id) > 0",
            name="ck_tax_review_decision_values",
        ),
    )
    op.create_index(
        "ix_tax_review_decision_batch", "tax_review_decisions", ["batch_id"]
    )
    with op.batch_alter_table("tax_journal_entries") as batch:
        batch.add_column(sa.Column("tax_review_decision_id", uuid, nullable=True))
        batch.create_foreign_key(
            "fk_tax_journal_review_decision",
            "tax_review_decisions",
            ["tax_review_decision_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("tax_journal_entries") as batch:
        batch.drop_constraint("fk_tax_journal_review_decision", type_="foreignkey")
        batch.drop_column("tax_review_decision_id")
    op.drop_index("ix_tax_review_decision_batch", table_name="tax_review_decisions")
    op.drop_table("tax_review_decisions")
