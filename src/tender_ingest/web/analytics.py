"""SQL-агрегаты для страницы /analytics (фаза B, docs/analytics-brief.md).

Только чтение. Когорты: вся база / relevant / избранные / участвовали — один и тот же
набор метрик по разным срезам. Метрики участия считаются всегда по участвовавшим
(они по определению про участие) независимо от выбранной когорты.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session

from tender_ingest.db.models import (
    Tender,
    TenderFavorite,
    TenderParticipation,
    TenderRelevance,
)

COHORTS = ("all", "relevant", "fav", "part")
COHORT_LABELS = {
    "all": "вся база",
    "relevant": "релевантные (ИИ)",
    "fav": "избранные",
    "part": "участвовали",
}


@dataclass
class MarketStats:
    total: int = 0
    duration_avg_days: float | None = None
    duration_median_days: float | None = None
    nmck_count: int = 0
    nmck_median: Decimal | None = None
    nmck_p25: Decimal | None = None
    nmck_p75: Decimal | None = None
    advance_share_pct: float | None = None
    advance_avg_pct: float | None = None


@dataclass
class MonthPoint:
    month: str  # "2026-05"
    count: int
    nmck_median: Decimal | None


@dataclass
class CustomerStat:
    name: str
    inn: str | None
    count: int
    nmck_median: Decimal | None


@dataclass
class ParticipationStats:
    counts: dict[str, int] = field(default_factory=dict)  # status -> count
    win_rate_pct: float | None = None  # won / (won + lost)
    reduction_avg_pct: float | None = None  # среднее снижение победителя от НМЦ
    our_gap_avg_pct: float | None = None  # наша цена vs победитель в проигранных
    avg_score_won: float | None = None
    avg_score_lost: float | None = None


class AnalyticsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _cohort_where(self, stmt: Select[Any], cohort: str) -> Select[Any]:
        if cohort == "relevant":
            return stmt.where(
                Tender.reestr_number.in_(
                    select(TenderRelevance.reestr_number).where(
                        TenderRelevance.verdict == "relevant"
                    )
                )
            )
        if cohort == "fav":
            return stmt.where(Tender.reestr_number.in_(select(TenderFavorite.reestr_number)))
        if cohort == "part":
            return stmt.where(Tender.reestr_number.in_(select(TenderParticipation.reestr_number)))
        return stmt

    def market(self, cohort: str) -> MarketStats:
        duration_days = (
            func.extract("epoch", Tender.submission_deadline - Tender.publish_date) / 86400.0
        )
        stmt = self._cohort_where(
            select(
                func.count(),
                func.avg(duration_days),
                func.percentile_cont(0.5).within_group(duration_days.asc()),
                func.count(Tender.nmck),
                func.percentile_cont(0.5).within_group(Tender.nmck.asc()),
                func.percentile_cont(0.25).within_group(Tender.nmck.asc()),
                func.percentile_cont(0.75).within_group(Tender.nmck.asc()),
                func.avg(case((Tender.advance_raw.is_not(None), 1.0), else_=0.0)) * 100,
                func.avg(Tender.advance_pct),
            ).select_from(Tender),
            cohort,
        )
        row = self.session.execute(stmt).one()
        return MarketStats(
            total=row[0] or 0,
            duration_avg_days=float(row[1]) if row[1] is not None else None,
            duration_median_days=float(row[2]) if row[2] is not None else None,
            nmck_count=row[3] or 0,
            nmck_median=Decimal(str(row[4])) if row[4] is not None else None,
            nmck_p25=Decimal(str(row[5])) if row[5] is not None else None,
            nmck_p75=Decimal(str(row[6])) if row[6] is not None else None,
            advance_share_pct=float(row[7]) if row[7] is not None else None,
            advance_avg_pct=float(row[8]) if row[8] is not None else None,
        )

    def monthly(self, cohort: str, months: int = 12) -> list[MonthPoint]:
        month = func.to_char(func.date_trunc("month", Tender.publish_date), "YYYY-MM")
        stmt = self._cohort_where(
            select(
                month,
                func.count(),
                func.percentile_cont(0.5).within_group(Tender.nmck.asc()),
            )
            .select_from(Tender)
            .where(Tender.publish_date.is_not(None))
            .group_by(month)
            .order_by(month.desc())
            .limit(months),
            cohort,
        )
        rows = self.session.execute(stmt).all()
        points = [
            MonthPoint(
                month=r[0],
                count=r[1],
                nmck_median=Decimal(str(r[2])) if r[2] is not None else None,
            )
            for r in rows
        ]
        return list(reversed(points))

    def top_customers(self, cohort: str, limit: int = 10) -> list[CustomerStat]:
        stmt = self._cohort_where(
            select(
                Tender.customer_name,
                Tender.customer_inn,
                func.count(),
                func.percentile_cont(0.5).within_group(Tender.nmck.asc()),
            )
            .select_from(Tender)
            .where(Tender.customer_name.is_not(None))
            .group_by(Tender.customer_name, Tender.customer_inn)
            .order_by(func.count().desc())
            .limit(limit),
            cohort,
        )
        return [
            CustomerStat(
                name=r[0],
                inn=r[1],
                count=r[2],
                nmck_median=Decimal(str(r[3])) if r[3] is not None else None,
            )
            for r in self.session.execute(stmt).all()
        ]

    def participation(self) -> ParticipationStats:
        counts: dict[str, int] = dict(
            self.session.execute(
                select(TenderParticipation.status, func.count()).group_by(
                    TenderParticipation.status
                )
            )
            .tuples()
            .all()
        )
        won, lost = counts.get("won", 0), counts.get("lost", 0)
        win_rate = 100.0 * won / (won + lost) if (won + lost) else None

        # Снижение победителя от НМЦ — по завершённым торгам с обеими цифрами.
        reduction = self.session.execute(
            select(func.avg((1 - TenderParticipation.winner_price / Tender.nmck) * 100))
            .select_from(TenderParticipation)
            .join(Tender, Tender.reestr_number == TenderParticipation.reestr_number)
            .where(
                TenderParticipation.winner_price.is_not(None),
                Tender.nmck.is_not(None),
                Tender.nmck > 0,
            )
        ).scalar_one()

        # Насколько наша цена выше победившей в проигранных — сигнал «мы завышаем».
        gap = self.session.execute(
            select(
                func.avg(
                    (TenderParticipation.our_price - TenderParticipation.winner_price)
                    / TenderParticipation.winner_price
                    * 100
                )
            ).where(
                TenderParticipation.status == "lost",
                TenderParticipation.our_price.is_not(None),
                TenderParticipation.winner_price.is_not(None),
                TenderParticipation.winner_price > 0,
            )
        ).scalar_one()

        def _avg_score(status: str) -> float | None:
            val = self.session.execute(
                select(func.avg(TenderRelevance.score))
                .select_from(TenderParticipation)
                .join(
                    TenderRelevance,
                    TenderRelevance.reestr_number == TenderParticipation.reestr_number,
                )
                .where(TenderParticipation.status == status)
            ).scalar_one()
            return float(val) if val is not None else None

        return ParticipationStats(
            counts=counts,
            win_rate_pct=win_rate,
            reduction_avg_pct=float(reduction) if reduction is not None else None,
            our_gap_avg_pct=float(gap) if gap is not None else None,
            avg_score_won=_avg_score("won"),
            avg_score_lost=_avg_score("lost"),
        )
