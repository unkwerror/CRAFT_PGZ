"""Отбор кейсов для RAG: похожие тендеры + агрегаты сегмента + фидбек-контрпримеры.

Кейсы собираются SQL-ом в момент запроса — то, что бюро внесло минуту назад
(исход, заметка), уже участвует в отборе. Векторов нет намеренно: на текущем
объёме сегментного матчинга (регион/закон + диапазон НМЦ) достаточно.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from tender_ingest.db.models import (
    AiRecommendation,
    RecommendationFeedback,
    Tender,
    TenderFavorite,
    TenderNote,
    TenderParticipation,
    TenderRelevance,
)
from tender_ingest.relevance.arbiter.prompt import build_card_block

_MAX_CASES = 8
_NMCK_BAND = (Decimal("0.3"), Decimal("3"))  # похожий бюджет: 0.3x–3x от целевого
_STATUS_RU = {
    "applied": "подали заявку, итог неизвестен",
    "rejected": "не допущены",
    "lost": "проиграли",
    "won": "выиграли",
}


@dataclass(frozen=True)
class CaseCorpus:
    target_block: str
    aggregates_block: str
    cases_block: str
    feedback_block: str
    n_cases: int


def _fmt_money(v: Decimal | None) -> str:
    if v is None:
        return "—"
    return f"{v:,.0f}".replace(",", " ") + " ₽"


def _segment_filter(stmt, target: Tender):  # type: ignore[no-untyped-def]
    """Похожесть: тот же регион ИЛИ тот же закон; НМЦ в диапазоне 0.3x–3x (если известна)."""
    conds = []
    if target.region_code:
        conds.append(Tender.region_code == target.region_code)
    if target.law:
        conds.append(Tender.law == target.law)
    if conds:
        stmt = stmt.where(or_(*conds))
    if target.nmck:
        stmt = stmt.where(
            Tender.nmck.is_not(None),
            Tender.nmck >= target.nmck * _NMCK_BAND[0],
            Tender.nmck <= target.nmck * _NMCK_BAND[1],
        )
    return stmt


def _select_cases(
    session: Session, target: Tender
) -> list[tuple[Tender, TenderParticipation | None, TenderRelevance | None, bool]]:
    """Похожие кейсы: участвовали -> избранные -> релевантные, максимум _MAX_CASES."""
    priority = case(
        (TenderParticipation.reestr_number.is_not(None), 0),
        (TenderFavorite.reestr_number.is_not(None), 1),
        (TenderRelevance.verdict == "relevant", 2),
        else_=3,
    )
    stmt = (
        select(Tender, TenderParticipation, TenderRelevance, TenderFavorite.reestr_number)
        .join(
            TenderParticipation,
            TenderParticipation.reestr_number == Tender.reestr_number,
            isouter=True,
        )
        .join(
            TenderRelevance,
            TenderRelevance.reestr_number == Tender.reestr_number,
            isouter=True,
        )
        .join(
            TenderFavorite,
            TenderFavorite.reestr_number == Tender.reestr_number,
            isouter=True,
        )
        .where(Tender.reestr_number != target.reestr_number)
        .where(priority < 3)  # случайный шум в кейсы не берём
        .order_by(priority.asc(), TenderRelevance.score.desc().nulls_last())
        .limit(_MAX_CASES)
    )
    stmt = _segment_filter(stmt, target)
    rows = session.execute(stmt).all()
    return [(t, p, r, fav is not None) for t, p, r, fav in rows]


def _segment_aggregates(session: Session, target: Tender) -> str:
    """Факты по сегменту, посчитанные SQL-ом — модель рассуждает над ними, не выдумывает."""
    part_stmt = (
        select(
            func.count(),
            func.sum(case((TenderParticipation.status == "won", 1), else_=0)),
            func.sum(case((TenderParticipation.status == "lost", 1), else_=0)),
            func.avg(
                case(
                    (
                        (TenderParticipation.winner_price.is_not(None)) & (Tender.nmck > 0),
                        (1 - TenderParticipation.winner_price / Tender.nmck) * 100,
                    ),
                )
            ),
        )
        .select_from(TenderParticipation)
        .join(Tender, Tender.reestr_number == TenderParticipation.reestr_number)
    )
    part_stmt = _segment_filter(part_stmt, target)
    n_part, won, lost, reduction = session.execute(part_stmt).one()

    duration_days = (
        func.extract("epoch", Tender.submission_deadline - Tender.publish_date) / 86400.0
    )
    market_stmt = _segment_filter(
        select(
            func.count(),
            func.percentile_cont(0.5).within_group(Tender.nmck.asc()),
            func.percentile_cont(0.5).within_group(duration_days.asc()),
            func.avg(case((Tender.advance_raw.is_not(None), 1.0), else_=0.0)) * 100,
        ).select_from(Tender),
        target,
    )
    n_market, nmck_med, dur_med, adv_share = session.execute(market_stmt).one()

    lines = [f"Закупок в сегменте (похожие по региону/закону и бюджету): {n_market}"]
    if nmck_med is not None:
        lines.append(f"Медианная НМЦ сегмента: {_fmt_money(Decimal(str(nmck_med)))}")
    if dur_med is not None:
        lines.append(f"Медианный срок на подачу заявки: {float(dur_med):.0f} дн.")
    if adv_share is not None:
        lines.append(f"Доля закупок с авансом: {float(adv_share):.0f}%")
    if n_part:
        lines.append(
            f"Наших участий в сегменте: {n_part} (выиграли {won or 0}, проиграли {lost or 0})"
        )
    if reduction is not None:
        lines.append(f"Среднее снижение победителя от НМЦ в наших торгах: {float(reduction):.1f}%")
    return "\n".join(lines)


def _case_block(
    session: Session,
    tender: Tender,
    part: TenderParticipation | None,
    rel: TenderRelevance | None,
    fav: bool,
) -> str:
    lines = [
        f"Тендер {tender.reestr_number}: {(tender.subject or 'без названия')[:200]}",
        f"  Регион: {tender.region_name or tender.region_code or '—'} · {tender.law or '—'}"
        f" · НМЦ {_fmt_money(tender.nmck)}" + (" · в избранном" if fav else ""),
    ]
    if part is not None:
        line = f"  ИСХОД: {_STATUS_RU.get(part.status, part.status)}"
        if part.our_price is not None:
            line += f"; наша цена {_fmt_money(part.our_price)}"
        if part.winner_price is not None:
            line += f"; цена победителя {_fmt_money(part.winner_price)}"
            if tender.nmck:
                drop = (1 - part.winner_price / tender.nmck) * 100
                line += f" (снижение {drop:.1f}%)"
        lines.append(line)
        if part.comment:
            lines.append(f"  Комментарий к участию: {part.comment[:300]}")
    if rel is not None and rel.summary:
        lines.append(f"  Оценка ИИ ({rel.score}/100): {rel.summary[:250]}")
    notes = (
        session.execute(
            select(TenderNote.text)
            .where(TenderNote.reestr_number == tender.reestr_number)
            .order_by(TenderNote.created_at.desc())
            .limit(3)
        )
        .scalars()
        .all()
    )
    for note in notes:
        lines.append(f"  Заметка бюро: {note[:300]}")
    return "\n".join(lines)


def _feedback_block(session: Session, limit: int = 3) -> str:
    """Последние «мимо» с комментариями — контрпримеры, чтобы не повторять промахи."""
    rows = session.execute(
        select(AiRecommendation.recommendation, RecommendationFeedback.comment)
        .join(
            RecommendationFeedback,
            RecommendationFeedback.recommendation_id == AiRecommendation.id,
        )
        .where(
            RecommendationFeedback.useful.is_(False),
            RecommendationFeedback.comment.is_not(None),
        )
        .order_by(RecommendationFeedback.created_at.desc())
        .limit(limit)
    ).all()
    if not rows:
        return ""
    lines = []
    for reco, comment in rows:
        gist = str(reco.get("rationale", ""))[:150] if isinstance(reco, dict) else ""
        lines.append(f"- Рекомендация «{gist}…» — оценка бюро: мимо. Почему: {comment[:200]}")
    return "\n".join(lines)


def build_case_corpus(session: Session, reestr_number: str) -> CaseCorpus | None:
    """Собрать весь контекст RAG для тендера. None — если тендер не найден."""
    target = session.get(Tender, reestr_number)
    if target is None:
        return None
    cases = _select_cases(session, target)
    cases_text = "\n\n".join(_case_block(session, t, p, r, f) for t, p, r, f in cases)
    return CaseCorpus(
        target_block=build_card_block(target),
        aggregates_block=_segment_aggregates(session, target),
        cases_block=cases_text,
        feedback_block=_feedback_block(session),
        n_cases=len(cases),
    )
