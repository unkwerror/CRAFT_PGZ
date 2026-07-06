"""Убрать ИИ-экономиста по кейсам (ai_recommendations + recommendation_feedback).

Пользователь оставил единственный контур экономики — расчёт по базе «Экономики»
(tender_economics) с ИИ-ревью. Рекомендации по кейсам и их фидбек удаляются.

Revision ID: 0012_drop_ai_recommendations
Revises: 0011_merge_economics_tracking
Create Date: 2026-07-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_drop_ai_recommendations"
down_revision: str | None = "0011_merge_economics_tracking"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("recommendation_feedback")
    op.drop_table("ai_recommendations")


def downgrade() -> None:
    op.create_table(
        "ai_recommendations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "reestr_number",
            sa.Text(),
            sa.ForeignKey("tenders.reestr_number", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model", sa.String(40), nullable=False),
        sa.Column("recommendation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_ai_recommendations_reestr_number", "ai_recommendations", ["reestr_number"])
    op.create_table(
        "recommendation_feedback",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "recommendation_id",
            sa.Integer(),
            sa.ForeignKey("ai_recommendations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("useful", sa.Boolean(), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_recommendation_feedback_recommendation_id",
        "recommendation_feedback",
        ["recommendation_id"],
    )
