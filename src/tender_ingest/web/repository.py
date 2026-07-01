"""Read-запросы для веба: список с фильтрами (в стиле Контур.Закупок), карточка, фасеты.

Только чтение — запись идёт через существующие pipeline/scorer. Джойним tenders с
tender_relevance (left join: незаскоренные тоже показываем).

Фильтры повторяют панель Контура в рамках доступных данных: ключевые слова
(точное/по словам + исключения), тип торгов и этап (мультивыбор), регион, заказчик,
способ отбора, площадка, НМЦ (диапазон + «без НМЦ»), обеспечения заявки/контракта
(диапазон + «без»), авансирование, даты публикации/дедлайна, СМП/СОНО. Плюс наши
поля релевантности (вердикт, score, кто решил).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Numeric, Select, func, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from tender_ingest.db.models import AnalysisQueue, Tender, TenderDocument, TenderRelevance

_SORTS = {"score", "nmck", "deadline", "publish"}
_VERDICTS = {"relevant", "maybe", "noise", "auction"}
_ADVANCE = {"with", "without"}
PAGE_SIZE = 50


def _clean(s: str | None) -> str | None:
    return (s or "").strip() or None


def _clean_list(xs: list[str] | None) -> list[str]:
    if not xs:
        return []
    return [c for x in xs if (c := _clean(x)) is not None]


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


def _to_date(s: str | None) -> dt.date | None:
    text = _clean(s)
    if text is None:
        return None
    try:
        return dt.date.fromisoformat(text)
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


@dataclass
class Filters:
    # ключевые слова
    search: str | None = None
    exact: bool = False  # точное соответствие (фраза целиком) vs по словам
    exclude: str | None = None  # исключать слова
    # текст
    customer: str | None = None
    delivery: str | None = None
    # мультивыбор
    laws: list[str] = field(default_factory=list)  # тип торгов
    stages: list[str] = field(default_factory=list)  # этап
    # селекты
    verdict: str | None = None
    region_code: str | None = None
    purchase_method: str | None = None
    etp: str | None = None
    smp_sono: str | None = None
    decided_by: str | None = None
    currency: str | None = None
    source: str | None = None
    # НМЦ
    nmck_min: Decimal | None = None
    nmck_max: Decimal | None = None
    nmck_none: bool = False  # включать закупки без НМЦ
    # обеспечения
    bid_min: Decimal | None = None
    bid_max: Decimal | None = None
    bid_none: bool = False  # без обеспечения заявки
    contract_min: Decimal | None = None
    contract_max: Decimal | None = None
    contract_none: bool = False  # без обеспечения контракта
    # авансирование
    advance: str | None = None  # with | without
    # score
    score_min: int | None = None
    score_max: int | None = None
    # даты
    publish_from: dt.date | None = None
    publish_to: dt.date | None = None
    deadline_from: dt.date | None = None
    deadline_to: dt.date | None = None
    # навигация
    sort: str = "score"
    page: int = 1

    @classmethod
    def from_query(  # noqa: PLR0913 — плоский разбор query-параметров формы фильтров
        cls,
        *,
        search: str | None = None,
        exact: str | None = None,
        exclude: str | None = None,
        customer: str | None = None,
        delivery: str | None = None,
        laws: list[str] | None = None,
        stages: list[str] | None = None,
        verdict: str | None = None,
        region_code: str | None = None,
        purchase_method: str | None = None,
        etp: str | None = None,
        smp_sono: str | None = None,
        decided_by: str | None = None,
        currency: str | None = None,
        source: str | None = None,
        nmck_min: str | None = None,
        nmck_max: str | None = None,
        nmck_none: str | None = None,
        bid_min: str | None = None,
        bid_max: str | None = None,
        bid_none: str | None = None,
        contract_min: str | None = None,
        contract_max: str | None = None,
        contract_none: str | None = None,
        advance: str | None = None,
        score_min: str | None = None,
        score_max: str | None = None,
        publish_from: str | None = None,
        publish_to: str | None = None,
        deadline_from: str | None = None,
        deadline_to: str | None = None,
        sort: str | None = None,
        page: str | None = None,
    ) -> Filters:
        """Прощающий разбор: мусор -> None/[], без 422 на кривой ввод в форме."""
        return cls(
            search=_clean(search),
            exact=_flag(exact),
            exclude=_clean(exclude),
            customer=_clean(customer),
            delivery=_clean(delivery),
            laws=_clean_list(laws),
            stages=_clean_list(stages),
            verdict=verdict if verdict in _VERDICTS else None,
            region_code=_clean(region_code),
            purchase_method=_clean(purchase_method),
            etp=_clean(etp),
            smp_sono=_clean(smp_sono),
            decided_by=_clean(decided_by),
            currency=_clean(currency),
            source=_clean(source),
            nmck_min=_to_decimal(nmck_min),
            nmck_max=_to_decimal(nmck_max),
            nmck_none=_flag(nmck_none),
            bid_min=_to_decimal(bid_min),
            bid_max=_to_decimal(bid_max),
            bid_none=_flag(bid_none),
            contract_min=_to_decimal(contract_min),
            contract_max=_to_decimal(contract_max),
            contract_none=_flag(contract_none),
            advance=advance if advance in _ADVANCE else None,
            score_min=_to_int(score_min),
            score_max=_to_int(score_max),
            publish_from=_to_date(publish_from),
            publish_to=_to_date(publish_to),
            deadline_from=_to_date(deadline_from),
            deadline_to=_to_date(deadline_to),
            sort=sort if sort in _SORTS else "score",
            page=max(1, _to_int(page) or 1),
        )

    def any_advanced(self) -> bool:
        """Активны ли фильтры сверх «поиск+сортировка» (для авто-раскрытия панели)."""
        return bool(
            self.exclude
            or self.customer
            or self.delivery
            or self.laws
            or self.stages
            or self.verdict
            or self.region_code
            or self.purchase_method
            or self.etp
            or self.smp_sono
            or self.decided_by
            or self.currency
            or self.source
            or self.nmck_min is not None
            or self.nmck_max is not None
            or self.bid_min is not None
            or self.bid_max is not None
            or self.bid_none
            or self.contract_min is not None
            or self.contract_max is not None
            or self.contract_none
            or self.advance
            or self.score_min is not None
            or self.score_max is not None
            or self.publish_from
            or self.publish_to
            or self.deadline_from
            or self.deadline_to
        )


@dataclass
class Facets:
    laws: list[str] = field(default_factory=list)
    regions: list[tuple[str, str]] = field(default_factory=list)  # (code, name)
    purchase_methods: list[str] = field(default_factory=list)
    stages: list[str] = field(default_factory=list)
    etps: list[str] = field(default_factory=list)
    smp_sono: list[str] = field(default_factory=list)
    decided_by: list[str] = field(default_factory=list)
    currencies: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    verdict_counts: dict[str, int] = field(default_factory=dict)
    total: int = 0


def _sec_amount(key: str) -> Any:
    """Числовое значение обеспечения из JSONB securities.<key>.amount_rub (₽)."""
    return Tender.securities[key]["amount_rub"].astext.cast(Numeric)


def _sec_raw(key: str) -> Any:
    """Сырое значение обеспечения securities.<key>.raw (NULL == обеспечения нет)."""
    return Tender.securities[key]["raw"].astext


class WebRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _apply_filters(self, stmt: Select[Any], f: Filters) -> Select[Any]:  # noqa: C901, PLR0912
        # --- ключевые слова ---
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
        # --- текст ---
        if f.customer:
            like = f"%{f.customer}%"
            stmt = stmt.where(
                or_(Tender.customer_name.ilike(like), Tender.customer_inn.ilike(like))
            )
        if f.delivery:
            stmt = stmt.where(Tender.delivery_place.ilike(f"%{f.delivery}%"))
        # --- мультивыбор ---
        if f.laws:
            stmt = stmt.where(Tender.law.in_(f.laws))
        if f.stages:
            stmt = stmt.where(Tender.stage.in_(f.stages))
        # --- селекты ---
        if f.verdict:
            stmt = stmt.where(TenderRelevance.verdict == f.verdict)
        if f.region_code:
            stmt = stmt.where(Tender.region_code == f.region_code)
        if f.purchase_method:
            stmt = stmt.where(Tender.purchase_method == f.purchase_method)
        if f.etp:
            stmt = stmt.where(Tender.etp == f.etp)
        if f.smp_sono:
            stmt = stmt.where(Tender.smp_sono == f.smp_sono)
        if f.decided_by:
            stmt = stmt.where(TenderRelevance.decided_by == f.decided_by)
        if f.currency:
            stmt = stmt.where(Tender.currency == f.currency)
        if f.source:
            stmt = stmt.where(Tender.source == f.source)
        # --- НМЦ (с опцией «включать без НМЦ») ---
        if f.nmck_min is not None:
            c = Tender.nmck >= f.nmck_min
            stmt = stmt.where(or_(c, Tender.nmck.is_(None)) if f.nmck_none else c)
        if f.nmck_max is not None:
            c = Tender.nmck <= f.nmck_max
            stmt = stmt.where(or_(c, Tender.nmck.is_(None)) if f.nmck_none else c)
        # --- обеспечение заявки ---
        if f.bid_none:
            stmt = stmt.where(_sec_raw("bid").is_(None))
        if f.bid_min is not None:
            stmt = stmt.where(_sec_amount("bid") >= f.bid_min)
        if f.bid_max is not None:
            stmt = stmt.where(_sec_amount("bid") <= f.bid_max)
        # --- обеспечение контракта ---
        if f.contract_none:
            stmt = stmt.where(_sec_raw("contract").is_(None))
        if f.contract_min is not None:
            stmt = stmt.where(_sec_amount("contract") >= f.contract_min)
        if f.contract_max is not None:
            stmt = stmt.where(_sec_amount("contract") <= f.contract_max)
        # --- авансирование ---
        if f.advance == "with":
            stmt = stmt.where(Tender.advance_raw.is_not(None))
        elif f.advance == "without":
            stmt = stmt.where(Tender.advance_raw.is_(None))
        # --- score ---
        if f.score_min is not None:
            stmt = stmt.where(TenderRelevance.score >= f.score_min)
        if f.score_max is not None:
            stmt = stmt.where(TenderRelevance.score <= f.score_max)
        # --- даты ---
        if f.publish_from is not None:
            stmt = stmt.where(Tender.publish_date >= f.publish_from)
        if f.publish_to is not None:
            stmt = stmt.where(Tender.publish_date < f.publish_to + dt.timedelta(days=1))
        if f.deadline_from is not None:
            stmt = stmt.where(Tender.submission_deadline >= f.deadline_from)
        if f.deadline_to is not None:
            stmt = stmt.where(Tender.submission_deadline < f.deadline_to + dt.timedelta(days=1))
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
        total = self.session.execute(select(func.count()).select_from(Tender)).scalar_one()
        return Facets(
            laws=self._distinct(Tender.law),
            regions=regions,
            purchase_methods=self._distinct(Tender.purchase_method),
            stages=self._distinct(Tender.stage),
            etps=self._distinct(Tender.etp),
            smp_sono=self._distinct(Tender.smp_sono),
            decided_by=self._distinct(TenderRelevance.decided_by),
            currencies=self._distinct(Tender.currency),
            sources=self._distinct(Tender.source),
            verdict_counts=verdict_counts,
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

    def delete(self, reestr_number: str, doc_id: int) -> None:
        doc = self.get(reestr_number, doc_id)
        if doc is not None:
            self.session.delete(doc)
            self.session.commit()
