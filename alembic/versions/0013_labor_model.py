"""Трудозатраты: ставки ролей бюро (labor_rates) и факт часов по проектам (labor_hours).

Приёмник данных для модели «себестоимость = часы × полная ставка роли». Данные
заполняет бюро через Excel-шаблон (tender labor-template -> labor-import). Расчёт
по часам включится, когда данные появятся.

Revision ID: 0013_labor_model
Revises: 0012_drop_ai_recommendations
Create Date: 2026-07-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_labor_model"
down_revision: str | None = "0012_drop_ai_recommendations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "labor_rates",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("role", sa.Text(), nullable=False, unique=True),
        sa.Column("monthly_salary", sa.Numeric(12, 2), nullable=False),
        sa.Column("tax_coef", sa.Numeric(6, 3), nullable=False),
        sa.Column("overhead_coef", sa.Numeric(6, 3), nullable=False),
        sa.Column("fund_hours", sa.Numeric(7, 1), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "labor_hours",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("project_title", sa.Text(), nullable=False),
        sa.Column("canon", sa.String(32), nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("hours", sa.Numeric(9, 1), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_labor_hours_canon", "labor_hours", ["canon"])


def downgrade() -> None:
    op.drop_index("ix_labor_hours_canon", table_name="labor_hours")
    op.drop_table("labor_hours")
    op.drop_table("labor_rates")
