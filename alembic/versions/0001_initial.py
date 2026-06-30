"""initial schema: tenders, tender_raw, analysis_queue, ingestion_runs

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenders",
        sa.Column("reestr_number", sa.Text(), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("subject", sa.Text()),
        sa.Column("nmck", sa.Numeric(18, 2)),
        sa.Column("currency", sa.String(8)),
        sa.Column("law", sa.String(32)),
        sa.Column("purchase_method", sa.Text()),
        sa.Column("stage", sa.Text()),
        sa.Column("etp", sa.Text()),
        sa.Column("smp_sono", sa.Text()),
        sa.Column("publish_date", sa.DateTime()),
        sa.Column("submission_deadline", sa.DateTime()),
        sa.Column("delivery_place", sa.Text()),
        sa.Column("securities", JSONB(), server_default="{}"),
        sa.Column("advance_raw", sa.Text()),
        sa.Column("advance_pct", sa.Numeric(7, 2)),
        sa.Column("customer_name", sa.Text()),
        sa.Column("customer_inn", sa.String(16)),
        sa.Column("customer_kpp", sa.String(16)),
        sa.Column("region_code", sa.String(8)),
        sa.Column("region_name", sa.Text()),
        sa.Column("result", JSONB(), server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_tenders_region_code", "tenders", ["region_code"])
    op.create_index("ix_tenders_law", "tenders", ["law"])
    op.create_index("ix_tenders_publish_date", "tenders", ["publish_date"])

    op.create_table(
        "tender_raw",
        sa.Column("reestr_number", sa.Text(), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "analysis_queue",
        sa.Column(
            "reestr_number",
            sa.Text(),
            sa.ForeignKey("tenders.reestr_number", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "enqueued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_analysis_queue_status", "analysis_queue", ["status"])

    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("file", sa.Text()),
        sa.Column("rows_total", sa.Integer(), server_default="0"),
        sa.Column("tenders_upserted", sa.Integer(), server_default="0"),
        sa.Column("parse_failures", sa.Integer(), server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("error", sa.Text()),
    )


def downgrade() -> None:
    op.drop_table("ingestion_runs")
    op.drop_index("ix_analysis_queue_status", table_name="analysis_queue")
    op.drop_table("analysis_queue")
    op.drop_table("tender_raw")
    op.drop_index("ix_tenders_publish_date", table_name="tenders")
    op.drop_index("ix_tenders_law", table_name="tenders")
    op.drop_index("ix_tenders_region_code", table_name="tenders")
    op.drop_table("tenders")
