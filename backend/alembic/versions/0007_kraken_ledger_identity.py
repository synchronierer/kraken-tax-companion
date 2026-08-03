"""Add source-independent Kraken ledger identity.

Revision ID: 0007_kraken_ledger_identity
Revises: 0006_fifo_tax_journal_exports
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_kraken_ledger_identity"
down_revision: str | None = "0006_fifo_tax_journal_exports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("raw_import_records") as batch:
        batch.add_column(sa.Column("canonical_key", sa.String(512), nullable=True))
    connection = op.get_bind()
    records = sa.table(
        "raw_import_records",
        sa.column("id", sa.Uuid()),
        sa.column("external_id", sa.String()),
        sa.column("canonical_key", sa.String()),
    )
    rows = connection.execute(
        sa.select(records.c.id, records.c.external_id).where(
            records.c.external_id.like("kraken:ledger:%")
        )
    ).all()
    counts: dict[str, int] = {}
    for _, external_id in rows:
        counts[external_id] = counts.get(external_id, 0) + 1
    for record_id, external_id in rows:
        if counts[external_id] == 1:
            ledger_id = external_id.removeprefix("kraken:ledger:")
            connection.execute(
                records.update()
                .where(records.c.id == record_id)
                .values(canonical_key=f"kraken:spot_ledger:{ledger_id}")
            )
    with op.batch_alter_table("raw_import_records") as batch:
        batch.create_unique_constraint("uq_raw_import_canonical_key", ["canonical_key"])


def downgrade() -> None:
    with op.batch_alter_table("raw_import_records") as batch:
        batch.drop_constraint("uq_raw_import_canonical_key", type_="unique")
        batch.drop_column("canonical_key")
