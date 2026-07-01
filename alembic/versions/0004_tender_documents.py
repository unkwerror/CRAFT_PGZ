"""tender_documents: файлы по тендеру (ТЗ, документация) для детального анализа

Revision ID: 0004_tender_documents
Revises: 0003_relevance_summary
Create Date: 2026-07-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_tender_documents"
down_revision: str | None = "0003_relevance_summary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tender_documents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "reestr_number",
            sa.Text(),
            sa.ForeignKey("tenders.reestr_number", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(160)),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column(
            "uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_tender_documents_reestr", "tender_documents", ["reestr_number"])


def downgrade() -> None:
    op.drop_index("ix_tender_documents_reestr", table_name="tender_documents")
    op.drop_table("tender_documents")
