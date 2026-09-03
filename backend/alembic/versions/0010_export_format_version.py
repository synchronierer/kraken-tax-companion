"""Version export formats independently from tax calculation rules.

Revision ID: 0010_export_format_version
Revises: 0009_tax_review_decisions
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_export_format_version"
down_revision: str | None = "0009_tax_review_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_VERSIONS = {
    "TAX_REPORT_PDF": "tax-report-pdf-v1",
    "TAX_JOURNAL_CSV": "tax-journal-csv-v1",
    "FIFO_ALLOCATIONS_CSV": "fifo-allocations-csv-v1",
    "INVENTORY_CSV": "inventory-csv-v1",
    "VALUATION_EVIDENCE_CSV": "valuation-evidence-csv-v1",
    "REVIEWS_CSV": "reviews-csv-v1",
    "ANNUAL_SUMMARY_CSV": "annual-summary-csv-v1",
}


def upgrade() -> None:
    with op.batch_alter_table("export_runs") as batch:
        batch.add_column(sa.Column("format_version", sa.String(64), nullable=True))
        batch.drop_constraint("uq_export_run_kind", type_="unique")
    table = sa.table(
        "export_runs",
        sa.column("kind", sa.String()),
        sa.column("format_version", sa.String()),
    )
    for kind, version in LEGACY_VERSIONS.items():
        op.execute(
            table.update().where(table.c.kind == kind).values(format_version=version)
        )
    with op.batch_alter_table("export_runs") as batch:
        batch.alter_column(
            "format_version", existing_type=sa.String(64), nullable=False
        )
        batch.create_unique_constraint(
            "uq_export_run_format",
            ["tax_calculation_run_id", "kind", "format_version"],
        )


def downgrade() -> None:
    connection = op.get_bind()
    duplicate = connection.execute(
        sa.text(
            "SELECT tax_calculation_run_id, kind FROM export_runs "
            "GROUP BY tax_calculation_run_id, kind HAVING count(*) > 1"
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "Downgrade requires at most one export format per tax run and kind."
        )
    with op.batch_alter_table("export_runs") as batch:
        batch.drop_constraint("uq_export_run_format", type_="unique")
        batch.create_unique_constraint(
            "uq_export_run_kind", ["tax_calculation_run_id", "kind"]
        )
        batch.drop_column("format_version")
