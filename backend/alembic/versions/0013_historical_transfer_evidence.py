"""Persist auditable external historical transfer evidence.

Revision ID: 0013_historical_transfer_evidence
Revises: 0012_financial_review_resolution
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.database.types import STRUCTURED_JSON, UtcDateTime

revision: str = "0013_historical_transfer_evidence"
down_revision: str | None = "0012_financial_review_resolution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid = sa.Uuid()
    amount = sa.Numeric(38, 18).with_variant(sa.String(80), "sqlite")
    op.create_table(
        "historical_transfer_links",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("stable_key", sa.String(512), nullable=False, unique=True),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("evidence_type", sa.String(128), nullable=False),
        sa.Column("source_record_ids", STRUCTURED_JSON, nullable=False),
        sa.Column(
            "target_raw_import_record_id",
            uuid,
            sa.ForeignKey("raw_import_records.id", ondelete="RESTRICT"),
        ),
        sa.Column("target_fingerprint", sa.String(128)),
        sa.Column("canonical_asset", sa.String(32), nullable=False),
        sa.Column("source_quantity", amount, nullable=False),
        sa.Column("target_quantity", amount, nullable=False),
        sa.Column(
            "transfer_nature",
            sa.Enum(
                "SELF_TRANSFER",
                "EXTERNAL_RECEIPT",
                "UNKNOWN",
                name="historicaltransfernature",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "basis_coverage",
            sa.Enum(
                "COMPLETE",
                "PARTIAL",
                "UNKNOWN",
                name="historicalbasiscoverage",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("source_fee_quantity", amount, nullable=False),
        sa.Column("source_fee_asset", sa.String(32)),
        sa.Column("transport_difference", amount, nullable=False),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("resolution_hash", sa.String(64), nullable=False),
        sa.Column(
            "parent_link_id",
            uuid,
            sa.ForeignKey("historical_transfer_links.id", ondelete="RESTRICT"),
        ),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.CheckConstraint(
            "source_quantity >= 0 AND target_quantity >= 0 "
            "AND source_fee_quantity >= 0 AND transport_difference >= 0",
            name="ck_historical_transfer_link_amounts",
        ),
        sa.UniqueConstraint(
            "evidence_hash",
            "resolution_hash",
            name="uq_historical_transfer_evidence",
        ),
    )
    op.create_table(
        "historical_transfer_resolutions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "historical_transfer_link_id",
            uuid,
            sa.ForeignKey("historical_transfer_links.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("stable_key", sa.String(512), nullable=False, unique=True),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "RESOLVED",
                "PARTIAL",
                "REVIEW_REQUIRED",
                name="historicalresolutionstatus",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "basis_coverage",
            sa.Enum(
                "COMPLETE",
                "PARTIAL",
                "UNKNOWN",
                name="historicalbasiscoverage_resolution",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("evidence_level", sa.String(128), nullable=False),
        sa.Column("resolution_hash", sa.String(64), nullable=False),
        sa.Column("explanation", sa.String(2048), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("resolved_at", UtcDateTime()),
    )


def downgrade() -> None:
    op.drop_table("historical_transfer_resolutions")
    op.drop_table("historical_transfer_links")
