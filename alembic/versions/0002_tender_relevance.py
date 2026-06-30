"""tender_relevance: оценка релевантности (Фаза 1)

Revision ID: 0002_tender_relevance
Revises: 0001_initial
Create Date: 2026-06-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002_tender_relevance"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tender_relevance",
        sa.Column(
            "reestr_number",
            sa.Text(),
            sa.ForeignKey("tenders.reestr_number", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("decided_by", sa.String(16), nullable=False),
        sa.Column("matched", JSONB(), server_default="[]"),
        sa.Column("anti_matched", JSONB(), server_default="[]"),
        sa.Column("llm_reason", sa.Text()),
        sa.Column(
            "scored_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_tender_relevance_verdict", "tender_relevance", ["verdict"])
    op.create_index("ix_tender_relevance_score", "tender_relevance", ["score"])


def downgrade() -> None:
    op.drop_index("ix_tender_relevance_score", table_name="tender_relevance")
    op.drop_index("ix_tender_relevance_verdict", table_name="tender_relevance")
    op.drop_table("tender_relevance")
