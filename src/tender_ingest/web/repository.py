"""Read-запросы для веба: список с фильтрами/сортировкой, карточка, фасеты, статистика.

Только чтение — запись идёт через существующие pipeline/scorer. Джойним tenders с
tender_relevance (left join: незаскоренные тоже показываем).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from tender_ingest.db.models import Tender, TenderRelevance

_SORTS = {"score", "nmck", "deadline", "publish"}
_VERDICTS = {"relevant", "maybe", "noise"}
PAGE_SIZE = 50


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


@dataclass
class Filters:
    verdict: str | None = None
    law: str | None = None
    region_code: str | None = None
    search: str | None = None
    sort: str = "score"
    page: int = 1

    def normalized(self) -> Filters:
        return Filters(
            verdict=self.verdict if self.verdict in _VERDICTS else None,
            law=self.law or None,
            region_code=self.region_code or None,
            search=(self.search or "").strip() or None,
            sort=self.sort if self.sort in _SORTS else "score",
            page=max(1, self.page),
        )


@dataclass
class Facets:
    laws: list[str] = field(default_factory=list)
    regions: list[tuple[str, str]] = field(default_factory=list)  # (code, name)
    verdict_counts: dict[str, int] = field(default_factory=dict)
    total: int = 0


class WebRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _apply_filters(self, stmt: Select[Any], f: Filters) -> Select[Any]:
        if f.verdict:
            stmt = stmt.where(TenderRelevance.verdict == f.verdict)
        if f.law:
            stmt = stmt.where(Tender.law == f.law)
        if f.region_code:
            stmt = stmt.where(Tender.region_code == f.region_code)
        if f.search:
            stmt = stmt.where(Tender.subject.ilike(f"%{f.search}%"))
        return stmt

    def list_tenders(self, f: Filters) -> tuple[list[TenderRow], int]:
        f = f.normalized()
        base = select(Tender, TenderRelevance).join(
            TenderRelevance,
            TenderRelevance.reestr_number == Tender.reestr_number,
            isouter=True,
        )
        base = self._apply_filters(base, f)

        count_stmt = self._apply_filters(
            select(func.count())
            .select_from(Tender)
            .join(
                TenderRelevance,
                TenderRelevance.reestr_number == Tender.reestr_number,
                isouter=True,
            ),
            f,
        )
        total = self.session.execute(count_stmt).scalar_one()

        stmt = base.order_by(*self._order(f.sort))
        stmt = stmt.limit(PAGE_SIZE).offset((f.page - 1) * PAGE_SIZE)

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
            select(Tender, TenderRelevance)
            .join(
                TenderRelevance,
                TenderRelevance.reestr_number == Tender.reestr_number,
                isouter=True,
            )
            .where(Tender.reestr_number == reestr_number)
        ).first()
        if row is None:
            return None
        return (row[0], row[1])

    def facets(self) -> Facets:
        laws = [
            r[0]
            for r in self.session.execute(
                select(Tender.law).where(Tender.law.is_not(None)).distinct().order_by(Tender.law)
            ).all()
        ]
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
        return Facets(laws=laws, regions=regions, verdict_counts=verdict_counts, total=total)
