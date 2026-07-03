"""document_analyses: бриф по ТЗ от LLM (семантический разбор + цитаты)

Revision ID: 0008_document_analyses
Revises: 0007_tender_uploads
Create Date: 2026-07-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_document_analyses"
down_revision: str | None = "0007_tender_uploads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_analyses",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("tender_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "reestr_number",
            sa.Text(),
            sa.ForeignKey("tenders.reestr_number", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model", sa.String(40), nullable=False),
        sa.Column("brief", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("pages", sa.Integer()),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_document_analyses_document", "document_analyses", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_document_analyses_document", table_name="document_analyses")
    op.drop_table("document_analyses")
