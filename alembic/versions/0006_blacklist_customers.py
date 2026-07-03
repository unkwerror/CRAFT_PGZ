"""blacklist_customers: стоп-лист заказчиков по ИНН (управляется из веба)

Revision ID: 0006_blacklist_customers
Revises: 0005_relevance_structured
Create Date: 2026-07-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_blacklist_customers"
down_revision: str | None = "0005_relevance_structured"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "blacklist_customers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("inn", sa.String(16), nullable=False),
        sa.Column("name", sa.Text()),
        sa.Column("reason", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_blacklist_customers_inn", "blacklist_customers", ["inn"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_blacklist_customers_inn", table_name="blacklist_customers")
    op.drop_table("blacklist_customers")
