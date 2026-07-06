"""ИИ-экономист (рекомендация по цене/участию): запуск, статус, фидбек «в точку/мимо»."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from tender_ingest.db.session import get_session_factory
from tender_ingest.web.recommend_job import job
from tender_ingest.web.security import require_auth
from tender_ingest.web.tracking import TrackingRepository

router = APIRouter(dependencies=[Depends(require_auth)])


def _detail(reestr_number: str, msg: str | None = None) -> RedirectResponse:
    target = f"/tender/{reestr_number}"
    if msg:
        target += f"?{urlencode({'msg': msg})}"
    return RedirectResponse(target, status_code=303)


@router.post("/tender/{reestr_number}/economics")
def run_economics(request: Request, reestr_number: str) -> RedirectResponse:
    """Запустить расчёт рекомендации в фоне (RAG: кейсы + агрегаты -> Claude)."""
    started = job.start(reestr_number)
    msg = (
        "Расчёт экономики запущен — займёт до минуты, страница обновится"
        if started
        else "Уже идёт другой расчёт — дождитесь завершения"
    )
    return _detail(reestr_number, msg)


@router.get("/tender/{reestr_number}/economics-status")
def economics_status(request: Request, reestr_number: str) -> JSONResponse:
    """Идёт ли расчёт ИМЕННО этого тендера (для поллинга с карточки)."""
    s = job.snapshot()
    return JSONResponse({"running": s.running and s.reestr_number == reestr_number})


@router.post("/tender/{reestr_number}/recommendation/{rec_id}/feedback")
def add_feedback(
    request: Request,
    reestr_number: str,
    rec_id: int,
    useful: Annotated[str, Form()] = "",
    comment: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """Оценка рекомендации. Промахи с комментарием попадают в промпт как контрпримеры."""
    is_useful = useful == "1"
    cleaned = (comment or "").strip() or None
    with get_session_factory()() as session:
        TrackingRepository(session).add_feedback(rec_id, useful=is_useful, comment=cleaned)
    return _detail(
        reestr_number,
        "Спасибо, учтено"
        if is_useful
        else "Учтено — промах попадёт в калибровку будущих рекомендаций",
    )
