"""Слияние веток миграций: экономика по базе «Экономики» + трекинг/ИИ-экономист.

Ветки развивались параллельно (0009_economics локально, 0009_participation_tracking ->
0010_ai_recommendations на сервере) — обе от 0008_document_analyses. Пустое слияние.

Revision ID: 0011_merge_economics_tracking
Revises: 0009_economics, 0010_ai_recommendations
Create Date: 2026-07-06
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0011_merge_economics_tracking"
down_revision: str | Sequence[str] | None = ("0009_economics", "0010_ai_recommendations")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
