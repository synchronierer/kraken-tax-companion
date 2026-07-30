"""Add provider-neutral raw-to-domain transformation persistence.

Revision ID: 0004_domain_transformation
Revises: 0003_import_batch_model
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_domain_transformation"
down_revision: str | None = "0003_import_batch_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

uuid = sa.Uuid(as_uuid=True)
timestamp = sa.DateTime(timezone=True)
amount = sa.Numeric(38, 18).with_variant(sa.String(80), "sqlite")
json = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def enum(*values: str) -> sa.Enum:
    return sa.Enum(*values, native_enum=False)


def projection_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("id", uuid, primary_key=True),
        sa.Column("stable_key", sa.String(512), nullable=False, unique=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("transformation_version", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("occurred_at", timestamp, nullable=False),
        sa.Column("created_at", timestamp, nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "transformation_runs",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("contract_version", sa.String(64), nullable=False),
        sa.Column(
            "status",
            enum(
                "CREATED",
                "PROCESSING",
                "COMPLETED",
                "COMPLETED_WITH_REVIEW",
                "FAILED",
            ),
            nullable=False,
        ),
        sa.Column("started_at", timestamp, nullable=False),
        sa.Column("completed_at", timestamp, nullable=True),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("checked_records", sa.Integer(), nullable=False),
        sa.Column("created_objects", sa.Integer(), nullable=False),
        sa.Column("internal_movements", sa.Integer(), nullable=False),
        sa.Column("review_cases", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("error_summary", sa.String(1024), nullable=True),
        sa.Column("created_at", timestamp, nullable=False),
        sa.CheckConstraint(
            "checked_records >= 0 AND created_objects >= 0 "
            "AND internal_movements >= 0 AND review_cases >= 0 "
            "AND error_count >= 0",
            name="ck_transformation_run_counts_non_negative",
        ),
    )
    op.create_table(
        "transformation_run_sessions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "transformation_run_id",
            uuid,
            sa.ForeignKey("transformation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "import_session_id",
            uuid,
            sa.ForeignKey("import_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "transformation_run_id",
            "import_session_id",
            name="uq_transformation_run_session",
        ),
    )
    op.create_table(
        "transformation_decisions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "raw_import_record_id",
            uuid,
            sa.ForeignKey("raw_import_records.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "import_session_id",
            uuid,
            sa.ForeignKey("import_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "transformation_run_id",
            uuid,
            sa.ForeignKey("transformation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("contract_version", sa.String(64), nullable=False),
        sa.Column(
            "decision_type",
            enum(
                "DOMAIN_EVENT_CREATED",
                "INTERNAL_MOVEMENT",
                "REVIEW_REQUIRED",
                "UNSUPPORTED",
                "DUPLICATE",
                "CONFLICT",
            ),
            nullable=False,
        ),
        sa.Column("reason_code", sa.String(128), nullable=False),
        sa.Column("explanation", sa.String(1024), nullable=False),
        sa.Column("domain_object_id", uuid, nullable=True),
        sa.Column("decided_at", timestamp, nullable=False),
        sa.UniqueConstraint(
            "transformation_run_id",
            "raw_import_record_id",
            name="uq_transformation_decision_run_raw",
        ),
    )
    op.create_table(
        "transformation_issues",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "transformation_run_id",
            uuid,
            sa.ForeignKey("transformation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "raw_import_record_id",
            uuid,
            sa.ForeignKey("raw_import_records.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("message", sa.String(1024), nullable=False),
        sa.Column("is_conflict", sa.Boolean(), nullable=False),
        sa.Column("occurred_at", timestamp, nullable=False),
    )
    op.create_table(
        "trade_executions",
        *projection_columns(),
        sa.Column("order_external_id", sa.String(255), nullable=False),
        sa.Column("raw_pair", sa.String(64), nullable=False),
        sa.Column("base_asset_raw", sa.String(32), nullable=False),
        sa.Column("base_asset", sa.String(32), nullable=False),
        sa.Column("quote_asset_raw", sa.String(32), nullable=False),
        sa.Column("quote_asset", sa.String(32), nullable=False),
        sa.Column("side", sa.String(16), nullable=False),
        sa.Column("order_type", sa.String(32), nullable=False),
        sa.Column("volume", amount, nullable=False),
        sa.Column("price", amount, nullable=False),
        sa.Column("cost", amount, nullable=False),
        sa.Column("fee", amount, nullable=False),
        sa.Column("fee_asset", sa.String(32), nullable=True),
        sa.Column(
            "reconciliation_status",
            enum("NOT_REQUIRED", "PENDING", "MATCHED", "PARTIAL", "CONFLICT"),
            nullable=False,
        ),
        sa.CheckConstraint("volume > 0", name="ck_trade_volume_positive"),
        sa.CheckConstraint("price > 0", name="ck_trade_price_positive"),
        sa.CheckConstraint("cost > 0", name="ck_trade_cost_positive"),
        sa.CheckConstraint("fee >= 0", name="ck_trade_fee_non_negative"),
    )
    op.create_table(
        "acquisition_lots",
        *projection_columns(),
        sa.Column("asset_raw_code", sa.String(32), nullable=False),
        sa.Column("asset_code", sa.String(32), nullable=False),
        sa.Column("asset_mapping_version", sa.String(64), nullable=False),
        sa.Column("quantity", amount, nullable=False),
        sa.Column(
            "acquisition_type",
            enum(
                "TRADE_BUY",
                "CRYPTO_EXCHANGE",
                "STAKING_REWARD",
                "LEGACY_STAKING_REWARD",
            ),
            nullable=False,
        ),
        sa.Column("account_scope", sa.String(64), nullable=False),
        sa.Column("wallet_scope", sa.String(64), nullable=False),
        sa.Column(
            "valuation_status",
            enum("VALUATION_REQUIRED", "NATIVE_EUR_AVAILABLE"),
            nullable=False,
        ),
        sa.Column(
            "tax_treatment_hint",
            enum(
                "PASSIVE_STAKING_REWARD",
                "LEGACY_STAKING_REWARD",
                "TRADE_ACQUISITION",
                "TRADE_DISPOSAL",
                "CRYPTO_ASSET_EXCHANGE",
                "CRYPTO_FEE_CANDIDATE",
            ),
            nullable=False,
        ),
        sa.Column("native_consideration_asset", sa.String(32), nullable=True),
        sa.Column("native_consideration_quantity", amount, nullable=True),
        sa.Column("fee_quantity", amount, nullable=False),
        sa.Column("fee_asset", sa.String(32), nullable=True),
        sa.Column("gross_quantity", amount, nullable=True),
        sa.CheckConstraint("quantity > 0", name="ck_acquisition_quantity_positive"),
        sa.CheckConstraint("fee_quantity >= 0", name="ck_acquisition_fee_non_negative"),
    )
    op.create_table(
        "disposal_events",
        *projection_columns(),
        sa.Column("asset_raw_code", sa.String(32), nullable=False),
        sa.Column("asset_code", sa.String(32), nullable=False),
        sa.Column("asset_mapping_version", sa.String(64), nullable=False),
        sa.Column("quantity", amount, nullable=False),
        sa.Column(
            "disposal_type",
            enum("TRADE_SELL", "CRYPTO_EXCHANGE", "CRYPTO_FEE"),
            nullable=False,
        ),
        sa.Column("account_scope", sa.String(64), nullable=False),
        sa.Column("wallet_scope", sa.String(64), nullable=False),
        sa.Column(
            "valuation_status",
            enum("VALUATION_REQUIRED", "NATIVE_EUR_AVAILABLE"),
            nullable=False,
        ),
        sa.Column(
            "tax_treatment_hint",
            enum(
                "PASSIVE_STAKING_REWARD",
                "LEGACY_STAKING_REWARD",
                "TRADE_ACQUISITION",
                "TRADE_DISPOSAL",
                "CRYPTO_ASSET_EXCHANGE",
                "CRYPTO_FEE_CANDIDATE",
            ),
            nullable=False,
        ),
        sa.Column("native_consideration_asset", sa.String(32), nullable=True),
        sa.Column("native_consideration_quantity", amount, nullable=True),
        sa.Column("fee_quantity", amount, nullable=False),
        sa.Column("fee_asset", sa.String(32), nullable=True),
        sa.Column("fifo_status", sa.String(32), nullable=False),
        sa.Column(
            "trade_execution_id",
            uuid,
            sa.ForeignKey("trade_executions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.CheckConstraint("quantity > 0", name="ck_disposal_quantity_positive"),
        sa.CheckConstraint("fee_quantity >= 0", name="ck_disposal_fee_non_negative"),
    )
    op.create_table(
        "fee_events",
        *projection_columns(),
        sa.Column("asset_code", sa.String(32), nullable=False),
        sa.Column("quantity", amount, nullable=False),
        sa.Column(
            "valuation_status",
            enum("VALUATION_REQUIRED", "NATIVE_EUR_AVAILABLE"),
            nullable=False,
        ),
        sa.Column("related_object_id", uuid, nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_fee_quantity_positive"),
    )
    op.create_table(
        "domain_provenance",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("domain_object_type", sa.String(64), nullable=False),
        sa.Column("domain_object_id", uuid, nullable=False),
        sa.Column(
            "raw_import_record_id",
            uuid,
            sa.ForeignKey("raw_import_records.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "import_session_id",
            uuid,
            sa.ForeignKey("import_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "transformation_run_id",
            uuid,
            sa.ForeignKey("transformation_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "domain_object_type",
            "domain_object_id",
            "raw_import_record_id",
            name="uq_domain_provenance_object_raw",
        ),
    )
    op.create_table(
        "valuation_requirements",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("asset_code", sa.String(32), nullable=False),
        sa.Column("target_currency", sa.String(32), nullable=False),
        sa.Column("valuation_at", timestamp, nullable=False),
        sa.Column("method", enum("DAILY_AVERAGE", "DIRECT_EUR"), nullable=False),
        sa.Column(
            "status",
            enum("VALUATION_REQUIRED", "NATIVE_EUR_AVAILABLE"),
            nullable=False,
        ),
        sa.Column("reason_code", sa.String(128), nullable=False),
        sa.Column("domain_object_type", sa.String(64), nullable=False),
        sa.Column("domain_object_id", uuid, nullable=False),
        sa.Column(
            "transformation_run_id",
            uuid,
            sa.ForeignKey("transformation_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", timestamp, nullable=False),
        sa.UniqueConstraint(
            "domain_object_type",
            "domain_object_id",
            name="uq_valuation_requirement_object",
        ),
    )


def downgrade() -> None:
    for table in (
        "valuation_requirements",
        "domain_provenance",
        "fee_events",
        "disposal_events",
        "acquisition_lots",
        "trade_executions",
        "transformation_issues",
        "transformation_decisions",
        "transformation_run_sessions",
        "transformation_runs",
    ):
        op.drop_table(table)
