"""Ручной запуск ИИ-скоринга: кнопка на списке -> POST /score -> оценка очереди."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from tender_ingest.relevance.scorer import score_pending
from tender_ingest.web.security import require_auth

router = APIRouter(dependencies=[Depends(require_auth)])


@router.post("/score")
def run_scoring(request: Request) -> RedirectResponse:
    """Оценить все неоценённые закупки (Claude). Блокирующий вызов ~десятки секунд."""
    summary = score_pending()
    msg = (
        f"Оценено {summary.total}: релевантных {summary.relevant}, "
        f"спорных {summary.maybe}, шум {summary.noise}"
    )
    return RedirectResponse(f"/?{urlencode({'msg': msg})}", status_code=303)
