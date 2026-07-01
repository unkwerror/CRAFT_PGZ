"""SQLAlchemy-модели (CLAUDE.md, раздел 9).

Минимум для Фазы 0: tenders (нормализованная закупка; обеспечения и результат —
JSONB прямо в строке, как разрешает раздел 9), tender_raw (сырьё строки целиком),
analysis_queue (очередь на анализ), ingestion_runs (журнал прогонов).
Upsert идёт по `reestr_number`, поэтому он же — первичный ключ tenders/tender_raw.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Tender(Base):
    __tablename__ = "tenders"

    reestr_number: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)

    subject: Mapped[str | None] = mapped_column(Text)
    nmck: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str | None] = mapped_column(String(8))
    law: Mapped[str | None] = mapped_column(String(32))
    purchase_method: Mapped[str | None] = mapped_column(Text)
    stage: Mapped[str | None] = mapped_column(Text)
    etp: Mapped[str | None] = mapped_column(Text)
    smp_sono: Mapped[str | None] = mapped_column(Text)
    publish_date: Mapped[dt.datetime | None] = mapped_column(DateTime)
    submission_deadline: Mapped[dt.datetime | None] = mapped_column(DateTime)
    delivery_place: Mapped[str | None] = mapped_column(Text)

    securities: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    advance_raw: Mapped[str | None] = mapped_column(Text)
    advance_pct: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))

    customer_name: Mapped[str | None] = mapped_column(Text)
    customer_inn: Mapped[str | None] = mapped_column(String(16))
    customer_kpp: Mapped[str | None] = mapped_column(String(16))
    region_code: Mapped[str | None] = mapped_column(String(8))
    region_name: Mapped[str | None] = mapped_column(Text)

    result: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TenderRaw(Base):
    __tablename__ = "tender_raw"

    reestr_number: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AnalysisQueue(Base):
    __tablename__ = "analysis_queue"

    reestr_number: Mapped[str] = mapped_column(
        Text, ForeignKey("tenders.reestr_number", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    enqueued_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    file: Mapped[str | None] = mapped_column(Text)
    rows_total: Mapped[int] = mapped_column(Integer, default=0)
    tenders_upserted: Mapped[int] = mapped_column(Integer, default=0)
    parse_failures: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    error: Mapped[str | None] = mapped_column(Text)


class TenderRelevance(Base):
    """Оценка закупки: score 0–100, вердикт, резюме от Claude и объективные факторы."""

    __tablename__ = "tender_relevance"

    reestr_number: Mapped[str] = mapped_column(
        Text, ForeignKey("tenders.reestr_number", ondelete="CASCADE"), primary_key=True
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)  # relevant|maybe|noise
    decided_by: Mapped[str] = mapped_column(String(16), nullable=False)  # rules|claude
    summary: Mapped[str | None] = mapped_column(Text)  # резюме под тендер
    factors: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)  # объективные факторы
    scored_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TenderDocument(Base):
    """Файл по тендеру (ТЗ, документация и пр.) для детального анализа. Байты — в БД."""

    __tablename__ = "tender_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reestr_number: Mapped[str] = mapped_column(
        Text, ForeignKey("tenders.reestr_number", ondelete="CASCADE"), index=True, nullable=False
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(160))
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    uploaded_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
