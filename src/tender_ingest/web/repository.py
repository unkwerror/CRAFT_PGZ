"""Read-запросы для веба: список с фильтрами, карточка, фасеты.

Только чтение — запись идёт через существующие pipeline/scorer. Джойним tenders с
tender_relevance (left join: незаскоренные тоже показываем).

Фильтры сознательно сведены к самым важным для бюро: ключевые слова (по словам или
фразой + исключения), вердикт ИИ, регион, закон, диапазон НМЦ, сортировка.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from tender_ingest.db.models import (
    AnalysisQueue,
    DocumentAnalysis,
    IngestionRun,
    Tender,
    TenderDocument,
    TenderRelevance,
    TenderUpload,
)

_SORTS = {"score", "nmck", "deadline", "publish"}
_VERDICTS = {"relevant", "maybe", "noise", "auction"}
PAGE_SIZE = 50


def _clean(s: str | None) -> str | None:
    return (s or "").strip() or None


def _to_decimal(s: str | None) -> Decimal | None:
    text = _clean(s)
    if text is None:
        return None
    try:
        return Decimal(text.replace(" ", "").replace(",", "."))
    except InvalidOperation:
        return None


def _to_int(s: str | None) -> int | None:
    text = _clean(s)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _flag(s: str | None) -> bool:
    return _clean(s) is not None


@dataclass
class TenderRow:
    reestr_number: str
    subject: str | None
    nmck: Decimal | None
    currency: str | None
    law: str | None
    region_code: str | None
    region_name: str | None
    etp: str | None
    submission_deadline: dt.datetime | None
    score: int | None
    verdict: str | None
    decided_by: str | None
    summary: str | None
    red_flags: list[str] | None


@dataclass
class Filters:
    search: str | None = None  # ключевые слова
    exact: bool = False  # точное соответствие (фраза целиком) vs по словам
    exclude: str | None = None  # исключать слова
    verdict: str | None = None  # вердикт ИИ
    region_code: str | None = None
    law: str | None = None  # тип торгов (44-ФЗ / 223-ФЗ / …)
    nmck_min: Decimal | None = None
    nmck_max: Decimal | None = None
    upload: int | None = None  # id выгрузки (ingestion_runs.id) — переключение выгрузок
    sort: str = "score"
    page: int = 1

    @classmethod
    def from_query(
        cls,
        *,
        search: str | None = None,
        exact: str | None = None,
        exclude: str | None = None,
        verdict: str | None = None,
        region_code: str | None = None,
        law: str | None = None,
        nmck_min: str | None = None,
        nmck_max: str | None = None,
        upload: str | None = None,
        sort: str | None = None,
        page: str | None = None,
    ) -> Filters:
        """Прощающий разбор: мусор -> None, без 422 на кривой ввод в форме."""
        return cls(
            search=_clean(search),
            exact=_flag(exact),
            exclude=_clean(exclude),
            verdict=verdict if verdict in _VERDICTS else None,
            region_code=_clean(region_code),
            law=_clean(law),
            nmck_min=_to_decimal(nmck_min),
            nmck_max=_to_decimal(nmck_max),
            upload=_to_int(upload),
            sort=sort if sort in _SORTS else "score",
            page=max(1, _to_int(page) or 1),
        )


@dataclass
class UploadOption:
    id: int
    file: str | None
    uploaded_at: dt.datetime
    count: int


@dataclass
class Facets:
    laws: list[str] = field(default_factory=list)
    regions: list[tuple[str, str]] = field(default_factory=list)  # (code, name)
    verdict_counts: dict[str, int] = field(default_factory=dict)
    uploads: list[UploadOption] = field(default_factory=list)
    total: int = 0


class WebRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _apply_filters(self, stmt: Select[Any], f: Filters) -> Select[Any]:
        if f.search:
            if f.exact:
                stmt = stmt.where(Tender.subject.ilike(f"%{f.search}%"))
            else:
                for word in f.search.split():
                    stmt = stmt.where(Tender.subject.ilike(f"%{word}%"))
        if f.exclude:
            for word in f.exclude.split():
                stmt = stmt.where(
                    or_(Tender.subject.notilike(f"%{word}%"), Tender.subject.is_(None))
                )
        if f.verdict:
            stmt = stmt.where(TenderRelevance.verdict == f.verdict)
        if f.region_code:
            stmt = stmt.where(Tender.region_code == f.region_code)
        if f.law:
            stmt = stmt.where(Tender.law == f.law)
        if f.nmck_min is not None:
            stmt = stmt.where(Tender.nmck >= f.nmck_min)
        if f.nmck_max is not None:
            stmt = stmt.where(Tender.nmck <= f.nmck_max)
        if f.upload is not None:
            stmt = stmt.where(
                Tender.reestr_number.in_(
                    select(TenderUpload.reestr_number).where(TenderUpload.run_id == f.upload)
                )
            )
        return stmt

    def _joined(self, *entities: Any) -> Select[Any]:
        return select(*entities).join(
            TenderRelevance,
            TenderRelevance.reestr_number == Tender.reestr_number,
            isouter=True,
        )

    def list_tenders(self, f: Filters) -> tuple[list[TenderRow], int]:
        total = self.session.execute(
            self._apply_filters(
                select(func.count())
                .select_from(Tender)
                .join(
                    TenderRelevance,
                    TenderRelevance.reestr_number == Tender.reestr_number,
                    isouter=True,
                ),
                f,
            )
        ).scalar_one()

        stmt = self._apply_filters(self._joined(Tender, TenderRelevance), f)
        stmt = stmt.order_by(*self._order(f.sort)).limit(PAGE_SIZE).offset((f.page - 1) * PAGE_SIZE)

        rows: list[TenderRow] = []
        for tender, rel in self.session.execute(stmt).all():
            rows.append(
                TenderRow(
                    reestr_number=tender.reestr_number,
                    subject=tender.subject,
                    nmck=tender.nmck,
                    currency=tender.currency,
                    law=tender.law,
                    region_code=tender.region_code,
                    region_name=tender.region_name,
                    etp=tender.etp,
                    submission_deadline=tender.submission_deadline,
                    score=rel.score if rel else None,
                    verdict=rel.verdict if rel else None,
                    decided_by=rel.decided_by if rel else None,
                    summary=rel.summary if rel else None,
                    red_flags=rel.red_flags if rel else None,
                )
            )
        return rows, total

    @staticmethod
    def _order(sort: str) -> tuple[Any, ...]:
        if sort == "nmck":
            return (Tender.nmck.desc().nulls_last(),)
        if sort == "deadline":
            return (Tender.submission_deadline.asc().nulls_last(),)
        if sort == "publish":
            return (Tender.publish_date.desc().nulls_last(),)
        # score (default): сначала самые релевантные
        return (TenderRelevance.score.desc().nulls_last(), Tender.publish_date.desc().nulls_last())

    def get(self, reestr_number: str) -> tuple[Tender, TenderRelevance | None] | None:
        row = self.session.execute(
            self._joined(Tender, TenderRelevance).where(Tender.reestr_number == reestr_number)
        ).first()
        if row is None:
            return None
        return (row[0], row[1])

    def _distinct(self, column: InstrumentedAttribute[Any]) -> list[str]:
        return [
            r[0]
            for r in self.session.execute(
                select(column).where(column.is_not(None)).distinct().order_by(column)
            ).all()
        ]

    def facets(self) -> Facets:
        regions = [
            (r[0], r[1])
            for r in self.session.execute(
                select(Tender.region_code, Tender.region_name)
                .where(Tender.region_code.is_not(None))
                .distinct()
                .order_by(Tender.region_code)
            ).all()
        ]
        verdict_counts = {
            r[0]: r[1]
            for r in self.session.execute(
                select(TenderRelevance.verdict, func.count()).group_by(TenderRelevance.verdict)
            ).all()
        }
        upload_rows = self.session.execute(
            select(
                IngestionRun.id,
                IngestionRun.file,
                IngestionRun.started_at,
                func.count(TenderUpload.reestr_number),
            )
            .join(TenderUpload, TenderUpload.run_id == IngestionRun.id)
            .where(IngestionRun.status == "success")
            .group_by(IngestionRun.id)
            .order_by(IngestionRun.started_at.desc())
        ).all()
        uploads = [UploadOption(r[0], r[1], r[2], r[3]) for r in upload_rows]
        total = self.session.execute(select(func.count()).select_from(Tender)).scalar_one()
        return Facets(
            laws=self._distinct(Tender.law),
            regions=regions,
            verdict_counts=verdict_counts,
            uploads=uploads,
            total=total,
        )

    def pending_count(self) -> int:
        """Сколько закупок ещё не оценено ИИ (в очереди со статусом 'pending')."""
        return self.session.execute(
            select(func.count()).select_from(AnalysisQueue).where(AnalysisQueue.status == "pending")
        ).scalar_one()


@dataclass
class DocMeta:
    id: int
    filename: str
    content_type: str | None
    size_bytes: int
    uploaded_at: dt.datetime


class DocumentRepository:
    """CRUD документов по тендеру (ТЗ, документация). Байты храним в БД (bytea)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for(self, reestr_number: str) -> list[DocMeta]:
        rows = self.session.execute(
            select(
                TenderDocument.id,
                TenderDocument.filename,
                TenderDocument.content_type,
                TenderDocument.size_bytes,
                TenderDocument.uploaded_at,
            )
            .where(TenderDocument.reestr_number == reestr_number)
            .order_by(TenderDocument.uploaded_at.desc())
        ).all()
        return [DocMeta(r[0], r[1], r[2], r[3], r[4]) for r in rows]

    def add(self, reestr_number: str, filename: str, content_type: str | None, data: bytes) -> None:
        self.session.add(
            TenderDocument(
                reestr_number=reestr_number,
                filename=filename,
                content_type=content_type,
                size_bytes=len(data),
                data=data,
            )
        )
        self.session.commit()

    def get(self, reestr_number: str, doc_id: int) -> TenderDocument | None:
        """Документ с байтами; сверяем принадлежность тендеру (защита от IDOR)."""
        return self.session.execute(
            select(TenderDocument).where(
                TenderDocument.id == doc_id, TenderDocument.reestr_number == reestr_number
            )
        ).scalar_one_or_none()

    def get_by_id(self, doc_id: int) -> TenderDocument | None:
        """Документ по id (для фонового разбора; принадлежность тендеру внутри записи)."""
        return self.session.get(TenderDocument, doc_id)

    def delete(self, reestr_number: str, doc_id: int) -> None:
        doc = self.get(reestr_number, doc_id)
        if doc is not None:
            self.session.delete(doc)
            self.session.commit()

    def add_analysis(
        self,
        document_id: int,
        reestr_number: str,
        *,
        model: str,
        brief: dict[str, object],
        pages: int | None,
        truncated: bool,
    ) -> None:
        """Сохранить разбор ТЗ (append-only) и зафиксировать."""
        self.session.add(
            DocumentAnalysis(
                document_id=document_id,
                reestr_number=reestr_number,
                model=model,
                brief=brief,
                pages=pages,
                truncated=truncated,
            )
        )
        self.session.commit()

    def latest_analyses_for(self, reestr_number: str) -> dict[int, DocumentAnalysis]:
        """Последний разбор по каждому документу тендера: {document_id: DocumentAnalysis}."""
        rows = (
            self.session.execute(
                select(DocumentAnalysis)
                .where(DocumentAnalysis.reestr_number == reestr_number)
                .order_by(DocumentAnalysis.document_id, DocumentAnalysis.created_at.desc())
                .distinct(DocumentAnalysis.document_id)
            )
            .scalars()
            .all()
        )
        return {a.document_id: a for a in rows}
