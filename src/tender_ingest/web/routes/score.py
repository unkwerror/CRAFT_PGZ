"""Ручной запуск ИИ-скоринга: кнопки на списке -> POST /score -> оценка очереди."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from tender_ingest.db.repository import RelevanceRepository
from tender_ingest.db.session import get_session_factory
from tender_ingest.relevance.scorer import score_pending
from tender_ingest.web.security import require_auth

router = APIRouter(dependencies=[Depends(require_auth)])


@router.post("/score")
def run_scoring(request: Request, all: Annotated[str | None, Form()] = None) -> RedirectResponse:
    """Оценить закупки. all=1 — сначала вернуть все в очередь (полная переоценка).

    Блокирующий вызов на десятки секунд (батчи Claude параллельно).
    """
    if all:
        with get_session_factory()() as session:
            RelevanceRepository(session).requeue_all()

    summary = score_pending()
    if summary.total == 0:
        msg = "Нечего оценивать — все закупки уже оценены"
    else:
        msg = (
            f"Оценено {summary.total}: подходят {summary.relevant}, возможно {summary.maybe}, "
            f"аукцион {summary.auction}, не подходят {summary.noise}"
        )
        if summary.skipped:
            msg += f"; пропущено без ключа {summary.skipped}"
    return RedirectResponse(f"/?{urlencode({'msg': msg})}", status_code=303)
