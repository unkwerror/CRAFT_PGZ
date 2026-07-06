"""Избранное, участие бюро в торгах и заметки (фаза A аналитики, docs/analytics-brief.md)

Revision ID: 0009_participation_tracking
Revises: 0008_document_analyses
Create Date: 2026-07-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_participation_tracking"
down_revision: str | None = "0008_document_analyses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tender_favorites",
        sa.Column(
            "reestr_number",
            sa.Text(),
            sa.ForeignKey("tenders.reestr_number", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("note", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "tender_participation",
        sa.Column(
            "reestr_number",
            sa.Text(),
            sa.ForeignKey("tenders.reestr_number", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(16), nullable=False),  # applied|rejected|lost|won
        sa.Column("our_price", sa.Numeric(18, 2)),
        sa.Column("winner_price", sa.Numeric(18, 2)),
        sa.Column("decided_at", sa.Date()),
        sa.Column("comment", sa.Text()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "tender_notes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "reestr_number",
            sa.Text(),
            sa.ForeignKey("tenders.reestr_number", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_tender_notes_reestr", "tender_notes", ["reestr_number"])


def downgrade() -> None:
    op.drop_index("ix_tender_notes_reestr", table_name="tender_notes")
    op.drop_table("tender_notes")
    op.drop_table("tender_participation")
    op.drop_table("tender_favorites")
