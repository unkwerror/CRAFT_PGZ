"""tender_relevance: структурные поля reasoning, confidence, red_flags

Revision ID: 0005_relevance_structured
Revises: 0004_tender_documents
Create Date: 2026-07-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_relevance_structured"
down_revision: str | None = "0004_tender_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tender_relevance", sa.Column("reasoning", sa.Text(), nullable=True))
    op.add_column("tender_relevance", sa.Column("confidence", sa.Integer(), nullable=True))
    op.add_column(
        "tender_relevance",
        sa.Column("red_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tender_relevance", "red_flags")
    op.drop_column("tender_relevance", "confidence")
    op.drop_column("tender_relevance", "reasoning")
