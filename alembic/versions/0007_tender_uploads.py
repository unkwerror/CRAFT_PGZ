"""tender_uploads: членство закупок в выгрузках + счётчики новых/известных

Revision ID: 0007_tender_uploads
Revises: 0006_blacklist_customers
Create Date: 2026-07-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_tender_uploads"
down_revision: str | None = "0006_blacklist_customers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ingestion_runs", sa.Column("tenders_new", sa.Integer(), nullable=True))
    op.add_column("ingestion_runs", sa.Column("tenders_existing", sa.Integer(), nullable=True))
    op.create_table(
        "tender_uploads",
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("ingestion_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "reestr_number",
            sa.Text(),
            sa.ForeignKey("tenders.reestr_number", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_tender_uploads_reestr", "tender_uploads", ["reestr_number"])


def downgrade() -> None:
    op.drop_index("ix_tender_uploads_reestr", table_name="tender_uploads")
    op.drop_table("tender_uploads")
    op.drop_column("ingestion_runs", "tenders_existing")
    op.drop_column("ingestion_runs", "tenders_new")
