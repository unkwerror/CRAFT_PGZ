"""tender_relevance: summary + factors вместо matched/anti_matched/llm_reason

Revision ID: 0003_relevance_summary
Revises: 0002_tender_relevance
Create Date: 2026-07-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0003_relevance_summary"
down_revision: str | None = "0002_tender_relevance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tender_relevance", sa.Column("summary", sa.Text()))
    op.add_column("tender_relevance", sa.Column("factors", JSONB(), server_default="{}"))
    op.drop_column("tender_relevance", "matched")
    op.drop_column("tender_relevance", "anti_matched")
    op.drop_column("tender_relevance", "llm_reason")


def downgrade() -> None:
    op.add_column("tender_relevance", sa.Column("llm_reason", sa.Text()))
    op.add_column("tender_relevance", sa.Column("anti_matched", JSONB(), server_default="[]"))
    op.add_column("tender_relevance", sa.Column("matched", JSONB(), server_default="[]"))
    op.drop_column("tender_relevance", "factors")
    op.drop_column("tender_relevance", "summary")
