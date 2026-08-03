"""Add explicit staking reward valuation components.

Revision ID: 0008_reward_valuation_components
Revises: 0007_kraken_ledger_identity
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_reward_valuation_components"
down_revision: str | None = "0007_kraken_ledger_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

amount = sa.Numeric(38, 18).with_variant(sa.String(80), "sqlite")


def enum(*values: str) -> sa.Enum:
    return sa.Enum(*values, native_enum=False)


def upgrade() -> None:
    with op.batch_alter_table("valuation_decisions") as batch:
        batch.add_column(sa.Column("gross_quantity", amount, nullable=True))
        batch.add_column(sa.Column("fee_quantity", amount, nullable=True))
        batch.add_column(sa.Column("net_quantity", amount, nullable=True))
        batch.add_column(sa.Column("gross_income_eur", amount, nullable=True))
        batch.add_column(sa.Column("fee_value_eur", amount, nullable=True))
        batch.add_column(sa.Column("net_acquisition_value_eur", amount, nullable=True))
        batch.add_column(sa.Column("valuation_basis", sa.String(128), nullable=True))
        batch.add_column(
            sa.Column(
                "fee_tax_classification",
                enum("NOT_APPLICABLE", "WERBUNGSKOSTEN_CANDIDATE"),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "fee_tax_review_status",
                enum("NOT_REQUIRED", "REVIEW_REQUIRED"),
                nullable=True,
            )
        )
        batch.create_check_constraint(
            "ck_valuation_reward_components",
            "(gross_quantity IS NULL OR gross_quantity > 0) AND "
            "(fee_quantity IS NULL OR fee_quantity >= 0) AND "
            "(net_quantity IS NULL OR net_quantity > 0) AND "
            "(gross_income_eur IS NULL OR gross_income_eur > 0) AND "
            "(fee_value_eur IS NULL OR fee_value_eur >= 0) AND "
            "(net_acquisition_value_eur IS NULL OR "
            "net_acquisition_value_eur > 0)",
        )


def downgrade() -> None:
    with op.batch_alter_table("valuation_decisions") as batch:
        batch.drop_constraint("ck_valuation_reward_components", type_="check")
        for name in (
            "fee_tax_review_status",
            "fee_tax_classification",
            "valuation_basis",
            "net_acquisition_value_eur",
            "fee_value_eur",
            "gross_income_eur",
            "net_quantity",
            "fee_quantity",
            "gross_quantity",
        ):
            batch.drop_column(name)
