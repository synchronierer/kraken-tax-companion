"""Add immutable EUR valuation evidence.

Revision ID: 0005_eur_valuation
Revises: 0004_domain_transformation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_eur_valuation"
down_revision: str | None = "0004_domain_transformation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
uuid = sa.Uuid(as_uuid=True)
ts = sa.DateTime(timezone=True)
amount = sa.Numeric(38, 18).with_variant(sa.String(80), "sqlite")
json = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def enum(*values: str) -> sa.Enum:
    return sa.Enum(*values, native_enum=False)


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "raw_import_records",
            "technical_metadata",
            existing_type=sa.JSON(),
            type_=postgresql.JSONB(),
            existing_nullable=False,
            postgresql_using="technical_metadata::jsonb",
        )
    op.create_table(
        "valuation_runs",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("contract_version", sa.String(64), nullable=False),
        sa.Column("method_version", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("correlation_id", uuid, nullable=False, unique=True),
        sa.Column("started_at", ts, nullable=False),
        sa.Column("ended_at", ts),
        sa.Column(
            "status",
            enum(
                "CREATED",
                "FETCHING",
                "APPLYING",
                "COMPLETED",
                "COMPLETED_WITH_REVIEW",
                "FAILED",
            ),
            nullable=False,
        ),
        *[
            sa.Column(name, sa.Integer, nullable=False)
            for name in (
                "checked_requirements",
                "resolved_requirements",
                "native_count",
                "automatic_count",
                "manual_count",
                "review_count",
                "error_count",
            )
        ],
        sa.Column("error_summary", sa.String(1024)),
        sa.CheckConstraint(
            "checked_requirements >= 0 AND resolved_requirements >= 0 "
            "AND native_count >= 0 AND automatic_count >= 0 "
            "AND manual_count >= 0 AND review_count >= 0 AND error_count >= 0",
            name="ck_valuation_run_counts",
        ),
    )
    op.create_table(
        "provider_evidence",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_contract_version", sa.String(64), nullable=False),
        sa.Column("provider_asset_id", sa.String(128), nullable=False),
        sa.Column("target_currency", sa.String(32), nullable=False),
        sa.Column("requested_from", ts, nullable=False),
        sa.Column("requested_to", ts, nullable=False),
        sa.Column("fetched_at", ts, nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column("response_hash", sa.String(64), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("earliest_observed_at", ts),
        sa.Column("latest_observed_at", ts),
        sa.Column("observations", json, nullable=False),
        sa.CheckConstraint(
            "http_status >= 100 AND http_status <= 599 AND observation_count >= 0 "
            "AND requested_to > requested_from AND target_currency = 'EUR' "
            "AND length(response_hash) = 64",
            name="ck_provider_evidence_values",
        ),
        sa.UniqueConstraint(
            "provider",
            "provider_contract_version",
            "provider_asset_id",
            "target_currency",
            "requested_from",
            "requested_to",
            "response_hash",
            name="uq_provider_evidence_identity",
        ),
    )
    op.create_table(
        "daily_prices",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("asset_code", sa.String(32), nullable=False),
        sa.Column("price_date", sa.Date(), nullable=False),
        sa.Column("unit_price_eur", amount, nullable=False),
        sa.Column(
            "method",
            enum(
                "NATIVE_EUR",
                "DAILY_AVERAGE_HOURLY",
                "MANUAL_DAILY_PRICE",
            ),
            nullable=False,
        ),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_contract_version", sa.String(64), nullable=False),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("sample_count", sa.Integer, nullable=False),
        sa.Column("earliest_sample_at", ts),
        sa.Column("latest_sample_at", ts),
        sa.Column("minimum_price_eur", amount),
        sa.Column("maximum_price_eur", amount),
        sa.Column("fetched_at", ts, nullable=False),
        sa.Column(
            "status",
            enum(
                "PENDING",
                "FETCHING",
                "RESOLVED",
                "REVIEW_REQUIRED",
                "FAILED",
                "SUPERSEDED",
            ),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("external_reference", sa.String(512)),
        sa.Column("reason", sa.String(1024)),
        sa.Column("entered_by", sa.String(255)),
        sa.Column("supersedes_id", uuid, sa.ForeignKey("daily_prices.id")),
        sa.Column(
            "provider_evidence_id",
            uuid,
            sa.ForeignKey("provider_evidence.id"),
        ),
        sa.CheckConstraint(
            "unit_price_eur > 0 AND sample_count >= 0 AND version > 0 "
            "AND length(evidence_hash) = 64",
            name="ck_daily_price_values",
        ),
        sa.UniqueConstraint(
            "asset_code",
            "price_date",
            "method",
            "provider",
            "provider_contract_version",
            "evidence_hash",
            name="uq_daily_price_evidence",
        ),
        sa.UniqueConstraint(
            "asset_code",
            "price_date",
            "method",
            "provider",
            "provider_contract_version",
            "version",
            name="uq_daily_price_version",
        ),
    )
    op.create_table(
        "valuation_decisions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "valuation_requirement_id",
            uuid,
            sa.ForeignKey("valuation_requirements.id"),
            nullable=False,
        ),
        sa.Column(
            "valuation_run_id",
            uuid,
            sa.ForeignKey("valuation_runs.id"),
            nullable=False,
        ),
        sa.Column("domain_object_type", sa.String(64), nullable=False),
        sa.Column("domain_object_id", uuid, nullable=False),
        sa.Column("asset_code", sa.String(32), nullable=False),
        sa.Column("quantity", amount, nullable=False),
        sa.Column("valuation_at", ts, nullable=False),
        sa.Column("price_date", sa.Date(), nullable=False),
        sa.Column(
            "method",
            enum(
                "NATIVE_EUR",
                "DAILY_AVERAGE_HOURLY",
                "MANUAL_DAILY_PRICE",
            ),
            nullable=False,
        ),
        sa.Column("unit_price_eur", amount, nullable=False),
        sa.Column("eur_value", amount, nullable=False),
        sa.Column("price_source", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column(
            "provider_object_id",
            uuid,
            sa.ForeignKey("daily_prices.id"),
        ),
        sa.Column("provider_contract_version", sa.String(64), nullable=False),
        sa.Column("method_version", sa.String(64), nullable=False),
        sa.Column("sample_count", sa.Integer, nullable=False),
        sa.Column("fetched_at", ts, nullable=False),
        sa.Column("decided_at", ts, nullable=False),
        sa.Column(
            "status",
            enum(
                "PENDING",
                "FETCHING",
                "RESOLVED",
                "REVIEW_REQUIRED",
                "FAILED",
                "SUPERSEDED",
            ),
            nullable=False,
        ),
        sa.Column("reason_code", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("rounding_rule", sa.String(64), nullable=False),
        sa.Column(
            "supersedes_id",
            uuid,
            sa.ForeignKey("valuation_decisions.id"),
        ),
        sa.Column(
            "provider_evidence_id",
            uuid,
            sa.ForeignKey("provider_evidence.id"),
        ),
        sa.CheckConstraint(
            "quantity > 0 AND unit_price_eur > 0 AND eur_value > 0 "
            "AND sample_count >= 0 AND version > 0",
            name="ck_valuation_decision_values",
        ),
        sa.UniqueConstraint(
            "valuation_requirement_id",
            "version",
            name="uq_valuation_decision_version",
        ),
    )


def downgrade() -> None:
    op.drop_table("valuation_decisions")
    op.drop_table("daily_prices")
    op.drop_table("provider_evidence")
    op.drop_table("valuation_runs")
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "raw_import_records",
            "technical_metadata",
            existing_type=postgresql.JSONB(),
            type_=sa.JSON(),
            existing_nullable=False,
            postgresql_using="technical_metadata::json",
        )
