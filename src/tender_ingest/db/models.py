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
    Boolean,
    Date,
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
    tenders_new: Mapped[int | None] = mapped_column(Integer)  # впервые увиденные в этой выгрузке
    tenders_existing: Mapped[int | None] = mapped_column(Integer)  # уже были (не переоцениваются)
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
    summary: Mapped[str | None] = mapped_column(Text)  # краткий вывод-рекомендация
    reasoning: Mapped[str | None] = mapped_column(Text)  # развёрнутое обоснование по рубрике
    confidence: Mapped[int | None] = mapped_column(Integer)  # 0–100, уверенность модели
    red_flags: Mapped[list[str] | None] = mapped_column(JSONB)  # риски (список строк)
    factors: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)  # объективные факторы
    scored_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TenderUpload(Base):
    """Членство закупки в выгрузке (many-to-many): одна и та же карточка может прийти

    из нескольких выгрузок (пересечение по номеру). Позволяет фильтровать список по
    выгрузке («переключение между выгрузками»). run_id — это ingestion_runs.id.
    """

    __tablename__ = "tender_uploads"

    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ingestion_runs.id", ondelete="CASCADE"), primary_key=True
    )
    reestr_number: Mapped[str] = mapped_column(
        Text, ForeignKey("tenders.reestr_number", ondelete="CASCADE"), primary_key=True
    )


class BlacklistCustomer(Base):
    """Стоп-лист заказчиков по ИНН (управляется из веба). Матчинг — в scorer/factors.

    Дополняет захардкоженный стоп-лист по имени (Россети/ЕЭСК) из relevance/factors.py:
    здесь бюро само ведёт список ИНН, с кем не работаем → жёсткое исключение из скоринга.
    """

    __tablename__ = "blacklist_customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inn: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
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


class EconomicsProject(Base):
    """Проект из таблицы «Экономика» — база знаний расчётов бюро.

    Источник статистики долей по разделам для расчёта цены тендера. Повторный импорт
    файла полностью заменяет содержимое (таблица — снимок одного workbook).
    """

    __tablename__ = "economics_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sheet: Mapped[str] = mapped_column(String(16), nullable=False)  # work | preliminary
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    contract_total: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    contract_note: Mapped[str | None] = mapped_column(Text)  # напр. «(40% на ПД)»
    cost_planned: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    cost_fact: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    profit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    meta: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    source_file: Mapped[str | None] = mapped_column(Text)
    imported_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EconomicsLine(Base):
    """Строка расчёта проекта «Экономики»: раздел работ с долей/суммами и исполнителем."""

    __tablename__ = "economics_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("economics_projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    name_raw: Mapped[str] = mapped_column(Text, nullable=False)
    canon: Mapped[str | None] = mapped_column(String(32), index=True)  # ключ canon.CATALOG
    pct: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))  # колонка «%» из файла
    planned: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    fact: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    fact_raw: Mapped[str | None] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)
    share: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))  # план / цена договора


class TenderEconomics(Base):
    """Расчёт экономики тендера (ИИ или правка человека). Append-only: показываем последний.

    payload — весь расчёт целиком: база, строки, накладные, итоги, сетка понижения,
    аналоги, предупреждения (структуру задаёт economics/engine.py).
    """

    __tablename__ = "tender_economics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reestr_number: Mapped[str] = mapped_column(
        Text, ForeignKey("tenders.reestr_number", ondelete="CASCADE"), index=True, nullable=False
    )
    created_by: Mapped[str] = mapped_column(String(8), nullable=False)  # ai | user
    model: Mapped[str | None] = mapped_column(String(40))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DocumentAnalysis(Base):
    """Бриф по ТЗ от LLM (семантический разбор + цитаты). Append-only: показываем последний."""

    __tablename__ = "document_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tender_documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    reestr_number: Mapped[str] = mapped_column(
        Text, ForeignKey("tenders.reestr_number", ondelete="CASCADE"), nullable=False
    )
    model: Mapped[str] = mapped_column(String(40), nullable=False)
    brief: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)  # summary+поля+findings
    pages: Mapped[int | None] = mapped_column(Integer)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TenderFavorite(Base):
    """Избранная закупка (звёздочка в вебе). Отдельная когорта в аналитике и приоритет в RAG."""

    __tablename__ = "tender_favorites"

    reestr_number: Mapped[str] = mapped_column(
        Text, ForeignKey("tenders.reestr_number", ondelete="CASCADE"), primary_key=True
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TenderParticipation(Base):
    """Факт участия бюро и исход торгов — скелет аналитики экономики и корпуса RAG.

    Заполняется вручную из веба. Одна запись на закупку (upsert): статус может меняться
    по мере хода торгов (подали -> проиграли/выиграли).
    """

    __tablename__ = "tender_participation"

    reestr_number: Mapped[str] = mapped_column(
        Text, ForeignKey("tenders.reestr_number", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # applied|rejected|lost|won
    our_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    winner_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    decided_at: Mapped[dt.date | None] = mapped_column(Date)
    comment: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TenderNote(Base):
    """Заметка бюро по закупке (свободный текст). Попадает в промпт ИИ-экономиста дословно."""

    __tablename__ = "tender_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reestr_number: Mapped[str] = mapped_column(
        Text, ForeignKey("tenders.reestr_number", ondelete="CASCADE"), index=True, nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LaborRate(Base):
    """Ставка роли бюро: полная стоимость часа = оклад × налоги × накладные / фонд часов.

    Заполняется импортом из Excel-шаблона (tender labor-template / labor-import).
    Повторный импорт заменяет содержимое (снимок одного файла).
    """

    __tablename__ = "labor_rates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role: Mapped[str] = mapped_column(Text, unique=True, nullable=False)  # ГИП, АР, КР…
    monthly_salary: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    tax_coef: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)  # напр. 1.302
    overhead_coef: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)  # офис и пр.
    fund_hours: Mapped[Decimal] = mapped_column(Numeric(7, 1), nullable=False)  # часов/мес
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LaborHours(Base):
    """Факт часов по завершённому проекту: канон раздела × роль -> часы.

    База для модели трудозатрат (себестоимость = часы × полная ставка роли).
    """

    __tablename__ = "labor_hours"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_title: Mapped[str] = mapped_column(Text, nullable=False)
    canon: Mapped[str | None] = mapped_column(String(32), index=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    hours: Mapped[Decimal] = mapped_column(Numeric(9, 1), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
