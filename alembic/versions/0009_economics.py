"""economics: база знаний «Экономики» (проекты + строки) и расчёты экономики тендеров

Revision ID: 0009_economics
Revises: 0008_document_analyses
Create Date: 2026-07-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_economics"
down_revision: str | None = "0008_document_analyses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "economics_projects",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("sheet", sa.String(16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("contract_total", sa.Numeric(18, 2)),
        sa.Column("contract_note", sa.Text()),
        sa.Column("cost_planned", sa.Numeric(18, 2)),
        sa.Column("cost_fact", sa.Numeric(18, 2)),
        sa.Column("profit", sa.Numeric(18, 2)),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_file", sa.Text()),
        sa.Column(
            "imported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "economics_lines",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("economics_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name_raw", sa.Text(), nullable=False),
        sa.Column("canon", sa.String(32)),
        sa.Column("pct", sa.Numeric(9, 6)),
        sa.Column("planned", sa.Numeric(18, 2)),
        sa.Column("fact", sa.Numeric(18, 2)),
        sa.Column("fact_raw", sa.Text()),
        sa.Column("comment", sa.Text()),
        sa.Column("share", sa.Numeric(9, 6)),
    )
    op.create_index("ix_economics_lines_project", "economics_lines", ["project_id"])
    op.create_index("ix_economics_lines_canon", "economics_lines", ["canon"])
    op.create_table(
        "tender_economics",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "reestr_number",
            sa.Text(),
            sa.ForeignKey("tenders.reestr_number", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(8), nullable=False),
        sa.Column("model", sa.String(40)),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_tender_economics_reestr", "tender_economics", ["reestr_number"])


def downgrade() -> None:
    op.drop_index("ix_tender_economics_reestr", table_name="tender_economics")
    op.drop_table("tender_economics")
    op.drop_index("ix_economics_lines_canon", table_name="economics_lines")
    op.drop_index("ix_economics_lines_project", table_name="economics_lines")
    op.drop_table("economics_lines")
    op.drop_table("economics_projects")
