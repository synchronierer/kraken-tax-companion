"""Add FIFO, tax journal and export persistence.

Revision ID: 0006_fifo_tax_journal_exports
Revises: 0005_eur_valuation
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_fifo_tax_journal_exports"
down_revision: str | None = "0005_eur_valuation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
uuid = sa.Uuid(as_uuid=True)
ts = sa.DateTime(timezone=True)
amount = sa.Numeric(38, 18).with_variant(sa.String(80), "sqlite")


def enum(*values: str) -> sa.Enum:
    return sa.Enum(*values, native_enum=False)


def upgrade() -> None:
    op.create_table(
        "tax_calculation_runs",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("rules_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "status",
            enum(
                "CREATED",
                "PROCESSING",
                "COMPLETED",
                "COMPLETED_WITH_REVIEW",
                "FAILED",
                "SUPERSEDED",
            ),
            nullable=False,
        ),
        sa.Column("started_at", ts, nullable=False),
        sa.Column("fifo_rule_version", sa.String(64), nullable=False),
        sa.Column("fee_rule_version", sa.String(64), nullable=False),
        sa.Column("classification_rule_version", sa.String(64), nullable=False),
        sa.Column("journal_rule_version", sa.String(64), nullable=False),
        sa.Column("export_format_version", sa.String(64), nullable=False),
        sa.Column("ended_at", ts),
        sa.Column("checked_events", sa.Integer(), nullable=False),
        sa.Column("created_allocations", sa.Integer(), nullable=False),
        sa.Column("created_journal_entries", sa.Integer(), nullable=False),
        sa.Column("review_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("error_summary", sa.String(1024)),
        sa.Column("supersedes_id", uuid, sa.ForeignKey("tax_calculation_runs.id")),
        sa.UniqueConstraint(
            "period_start",
            "period_end",
            "snapshot_hash",
            "rules_fingerprint",
            name="uq_tax_run_identity",
        ),
        sa.CheckConstraint(
            "period_end >= period_start AND checked_events >= 0 "
            "AND created_allocations >= 0 AND created_journal_entries >= 0 "
            "AND review_count >= 0 AND error_count >= 0",
            name="ck_tax_run_values",
        ),
    )
    op.create_table(
        "inventory_lots",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "tax_calculation_run_id",
            uuid,
            sa.ForeignKey("tax_calculation_runs.id"),
            nullable=False,
        ),
        sa.Column(
            "acquisition_lot_id",
            uuid,
            sa.ForeignKey("acquisition_lots.id"),
            nullable=False,
        ),
        sa.Column("asset_code", sa.String(32), nullable=False),
        sa.Column("original_quantity", amount, nullable=False),
        sa.Column("remaining_quantity", amount, nullable=False),
        sa.Column("acquired_at", ts, nullable=False),
        sa.Column("acquisition_value_eur", amount, nullable=False),
        sa.Column("acquisition_fee_eur", amount, nullable=False),
        sa.Column("remaining_cost_eur", amount, nullable=False),
        sa.Column(
            "valuation_decision_id",
            uuid,
            sa.ForeignKey("valuation_decisions.id"),
            nullable=False,
        ),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "tax_calculation_run_id",
            "acquisition_lot_id",
            name="uq_inventory_run_acquisition",
        ),
        sa.CheckConstraint(
            "original_quantity > 0 AND remaining_quantity >= 0 "
            "AND remaining_quantity <= original_quantity AND acquisition_value_eur > 0 "
            "AND acquisition_fee_eur >= 0 AND remaining_cost_eur >= 0 "
            "AND sequence >= 0",
            name="ck_inventory_lot_values",
        ),
    )
    op.create_index(
        "ix_inventory_asset_remaining",
        "inventory_lots",
        ["asset_code", "remaining_quantity"],
    )
    op.create_table(
        "lot_allocations",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "tax_calculation_run_id",
            uuid,
            sa.ForeignKey("tax_calculation_runs.id"),
            nullable=False,
        ),
        sa.Column(
            "disposal_event_id",
            uuid,
            sa.ForeignKey("disposal_events.id"),
            nullable=False,
        ),
        sa.Column(
            "inventory_lot_id", uuid, sa.ForeignKey("inventory_lots.id"), nullable=False
        ),
        sa.Column("allocated_quantity", amount, nullable=False),
        sa.Column("allocation_order", sa.Integer(), nullable=False),
        sa.Column("acquisition_cost_eur", amount, nullable=False),
        sa.Column("disposal_proceeds_eur", amount, nullable=False),
        sa.Column("disposal_fee_eur", amount, nullable=False),
        sa.Column("gain_loss_eur", amount, nullable=False),
        sa.Column("acquired_at", ts, nullable=False),
        sa.Column("disposed_at", ts, nullable=False),
        sa.Column("holding_seconds", sa.Integer(), nullable=False),
        sa.Column("fifo_rule_version", sa.String(64), nullable=False),
        sa.Column("fee_rule_version", sa.String(64), nullable=False),
        sa.Column("created_at", ts, nullable=False),
        sa.UniqueConstraint(
            "tax_calculation_run_id",
            "disposal_event_id",
            "allocation_order",
            name="uq_allocation_order",
        ),
        sa.CheckConstraint(
            "allocated_quantity > 0 AND allocation_order > 0 "
            "AND acquisition_cost_eur >= 0 AND disposal_proceeds_eur >= 0 "
            "AND disposal_fee_eur >= 0 AND holding_seconds >= 0",
            name="ck_lot_allocation_values",
        ),
    )
    op.create_index(
        "ix_allocations_disposal",
        "lot_allocations",
        ["disposal_event_id", "allocation_order"],
    )
    op.create_table(
        "disposal_calculations",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "tax_calculation_run_id",
            uuid,
            sa.ForeignKey("tax_calculation_runs.id"),
            nullable=False,
        ),
        sa.Column(
            "disposal_event_id",
            uuid,
            sa.ForeignKey("disposal_events.id"),
            nullable=False,
        ),
        sa.Column("quantity", amount, nullable=False),
        sa.Column("allocated_quantity", amount, nullable=False),
        sa.Column("proceeds_eur", amount, nullable=False),
        sa.Column("acquisition_cost_eur", amount, nullable=False),
        sa.Column("fees_eur", amount, nullable=False),
        sa.Column("gain_loss_eur", amount, nullable=False),
        sa.Column(
            "status", enum("RESOLVED", "REVIEW_REQUIRED", "SUPERSEDED"), nullable=False
        ),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "tax_calculation_run_id",
            "disposal_event_id",
            name="uq_disposal_calculation_run",
        ),
    )
    op.create_table(
        "tax_journal_entries",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "tax_calculation_run_id",
            uuid,
            sa.ForeignKey("tax_calculation_runs.id"),
            nullable=False,
        ),
        sa.Column("occurred_at", ts, nullable=False),
        sa.Column("tax_year", sa.Integer(), nullable=False),
        sa.Column(
            "entry_type",
            enum(
                "ACQUISITION",
                "EARN_INFLOW",
                "DISPOSAL",
                "EXCHANGE",
                "FEE",
                "REALIZED_GAIN",
                "REALIZED_LOSS",
                "REVIEW",
                "CORRECTION",
            ),
            nullable=False,
        ),
        sa.Column("asset_code", sa.String(32), nullable=False),
        sa.Column("quantity", amount, nullable=False),
        sa.Column("eur_value", amount, nullable=False),
        sa.Column("proceeds_eur", amount),
        sa.Column("acquisition_cost_eur", amount),
        sa.Column("gain_loss_eur", amount),
        sa.Column("holding_seconds", sa.Integer()),
        sa.Column("classification", sa.String(255), nullable=False),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column(
            "status", enum("RESOLVED", "REVIEW_REQUIRED", "SUPERSEDED"), nullable=False
        ),
        sa.Column("source_object_type", sa.String(64), nullable=False),
        sa.Column("source_object_id", uuid, nullable=False),
        sa.Column(
            "valuation_decision_id", uuid, sa.ForeignKey("valuation_decisions.id")
        ),
        sa.Column("lot_allocation_id", uuid, sa.ForeignKey("lot_allocations.id")),
        sa.Column("supersedes_id", uuid, sa.ForeignKey("tax_journal_entries.id")),
        sa.UniqueConstraint(
            "tax_calculation_run_id",
            "entry_type",
            "source_object_id",
            "lot_allocation_id",
            name="uq_tax_journal_source",
        ),
    )
    op.create_index(
        "ix_tax_journal_year_type", "tax_journal_entries", ["tax_year", "entry_type"]
    )
    op.create_table(
        "tax_review_cases",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "tax_calculation_run_id",
            uuid,
            sa.ForeignKey("tax_calculation_runs.id"),
            nullable=False,
        ),
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("message", sa.String(1024), nullable=False),
        sa.Column("source_object_type", sa.String(64), nullable=False),
        sa.Column("source_object_id", uuid, nullable=False),
        sa.Column("occurred_at", ts, nullable=False),
        sa.UniqueConstraint(
            "tax_calculation_run_id",
            "code",
            "source_object_id",
            name="uq_tax_review_source",
        ),
    )
    op.create_table(
        "export_runs",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "tax_calculation_run_id",
            uuid,
            sa.ForeignKey("tax_calculation_runs.id"),
            nullable=False,
        ),
        sa.Column(
            "kind",
            enum(
                "TAX_JOURNAL_CSV",
                "FIFO_ALLOCATIONS_CSV",
                "INVENTORY_CSV",
                "VALUATION_EVIDENCE_CSV",
                "REVIEWS_CSV",
                "ANNUAL_SUMMARY_CSV",
                "TAX_REPORT_PDF",
            ),
            nullable=False,
        ),
        sa.Column("status", enum("CREATED", "COMPLETED", "FAILED"), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("rules_fingerprint", sa.String(64), nullable=False),
        sa.Column("started_at", ts, nullable=False),
        sa.Column("completed_at", ts),
        sa.Column("error_summary", sa.String(1024)),
        sa.UniqueConstraint(
            "tax_calculation_run_id", "kind", name="uq_export_run_kind"
        ),
    )
    op.create_table(
        "export_artifacts",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "export_run_id",
            uuid,
            sa.ForeignKey("export_runs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "kind",
            enum(
                "TAX_JOURNAL_CSV",
                "FIFO_ALLOCATIONS_CSV",
                "INVENTORY_CSV",
                "VALUATION_EVIDENCE_CSV",
                "REVIEWS_CSV",
                "ANNUAL_SUMMARY_CSV",
                "TAX_REPORT_PDF",
            ),
            nullable=False,
        ),
        sa.Column("file_name", sa.String(255), nullable=False, unique=True),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256_hash", sa.String(64), nullable=False),
        sa.Column("created_at", ts, nullable=False),
        sa.CheckConstraint(
            "size_bytes >= 0 AND length(sha256_hash) = 64",
            name="ck_export_artifact_values",
        ),
    )


def downgrade() -> None:
    op.drop_table("export_artifacts")
    op.drop_table("export_runs")
    op.drop_table("tax_review_cases")
    op.drop_index("ix_tax_journal_year_type", table_name="tax_journal_entries")
    op.drop_table("tax_journal_entries")
    op.drop_table("disposal_calculations")
    op.drop_index("ix_allocations_disposal", table_name="lot_allocations")
    op.drop_table("lot_allocations")
    op.drop_index("ix_inventory_asset_remaining", table_name="inventory_lots")
    op.drop_table("inventory_lots")
    op.drop_table("tax_calculation_runs")
