"""Add financial review suggestions and resolutions.

Revision ID: 0012_financial_review_resolution
Revises: 0011_kraken_incremental_sync
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.database.types import STRUCTURED_JSON, UtcDateTime

revision: str = "0012_financial_review_resolution"
down_revision: str | None = "0011_kraken_incremental_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid, timestamp = sa.Uuid(), UtcDateTime()
    op.create_table(
        "financial_review_suggestions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "transformation_issue_id",
            uuid,
            sa.ForeignKey("transformation_issues.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "suggestion_type",
            sa.Enum(
                "OWN_ACCOUNT_FIAT_WITHDRAWAL",
                "POSSIBLE_DELISTING_LIQUIDATION",
                name="financialsuggestiontype",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "SUGGESTED", "REJECTED", name="suggestionstatus", native_enum=False
            ),
            nullable=False,
        ),
        sa.Column(
            "confidence",
            sa.Enum(
                "LOW", "MEDIUM", "HIGH", name="reviewconfidence", native_enum=False
            ),
            nullable=False,
        ),
        sa.Column("reasons", STRUCTURED_JSON, nullable=False),
        sa.Column("metadata", STRUCTURED_JSON, nullable=False),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("decided_at", timestamp),
        sa.Column("decided_by", sa.String(255)),
        sa.Column("decision_reason", sa.String(1024)),
        sa.UniqueConstraint(
            "transformation_issue_id",
            "suggestion_type",
            name="uq_financial_review_suggestion_issue_type",
        ),
    )
    op.create_table(
        "financial_review_resolutions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "transformation_issue_id",
            uuid,
            sa.ForeignKey("transformation_issues.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "resolution_type",
            sa.Enum(
                "OWN_ACCOUNT_FIAT_WITHDRAWAL",
                "DELISTING_LIQUIDATION",
                name="financialreviewtype",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("CONFIRMED", name="resolutionstatus", native_enum=False),
            nullable=False,
        ),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("decided_at", timestamp, nullable=False),
        sa.Column("decided_by", sa.String(255), nullable=False),
        sa.Column("reason", sa.String(1024), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column(
            "confidence",
            sa.Enum(
                "LOW",
                "MEDIUM",
                "HIGH",
                name="resolutionreviewconfidence",
                native_enum=False,
            ),
        ),
        sa.Column(
            "tax_mapping_status",
            sa.Enum(
                "NOT_REQUIRED", "PENDING", name="taxmappingstatus", native_enum=False
            ),
            nullable=False,
        ),
        sa.Column("metadata", STRUCTURED_JSON, nullable=False),
    )
    op.create_table(
        "financial_review_record_links",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "raw_import_record_id",
            uuid,
            sa.ForeignKey("raw_import_records.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "suggestion_id",
            uuid,
            sa.ForeignKey("financial_review_suggestions.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "resolution_id",
            uuid,
            sa.ForeignKey("financial_review_resolutions.id", ondelete="CASCADE"),
        ),
        sa.Column("role", sa.String(32), nullable=False),
        sa.CheckConstraint(
            "(suggestion_id IS NOT NULL AND resolution_id IS NULL) OR "
            "(suggestion_id IS NULL AND resolution_id IS NOT NULL)",
            name="ck_financial_review_link_one_parent",
        ),
        sa.UniqueConstraint(
            "suggestion_id",
            "raw_import_record_id",
            name="uq_financial_review_suggestion_raw",
        ),
        sa.UniqueConstraint(
            "resolution_id",
            "raw_import_record_id",
            name="uq_financial_review_resolution_raw",
        ),
    )
    op.create_index(
        "uq_financial_review_confirmed_raw",
        "financial_review_record_links",
        ["raw_import_record_id"],
        unique=True,
        sqlite_where=sa.text("resolution_id IS NOT NULL"),
        postgresql_where=sa.text("resolution_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_financial_review_confirmed_raw",
        table_name="financial_review_record_links",
    )
    op.drop_table("financial_review_record_links")
    op.drop_table("financial_review_resolutions")
    op.drop_table("financial_review_suggestions")
