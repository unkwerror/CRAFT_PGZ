"""ИИ-скоринг: кнопки на списке -> POST /score -> фоновый прогон, /score/status — прогресс.

Скоринг идёт в фоне (см. web/scoring_job): на 1000+ карточках прогон занимает минуты,
синхронный ответ упёрся бы в таймаут nginx. POST запускает поток и сразу редиректит.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from tender_ingest.web.scoring_job import job
from tender_ingest.web.security import require_auth

router = APIRouter(dependencies=[Depends(require_auth)])


@router.post("/score")
def run_scoring(request: Request, all: Annotated[str | None, Form()] = None) -> RedirectResponse:
    """Запустить скоринг в фоне. all=1 — сначала вернуть все в очередь (полная переоценка)."""
    started = job.start(requeue=bool(all))
    msg = (
        "ИИ-скоринг запущен в фоне — прогресс появится вверху списка"
        if started
        else "ИИ-скоринг уже идёт — дождитесь завершения"
    )
    return RedirectResponse(f"/?{urlencode({'msg': msg})}", status_code=303)


@router.get("/score/status")
def scoring_status(request: Request) -> JSONResponse:
    """Прогресс текущего прогона (для поллинга со страницы списка).

    У скоринга прогресс НАСТОЯЩИЙ (оценено/всего по батчам); ETA — по фактической
    скорости: прошло/оценено × осталось. До первого батча — «готовлю…» без процентов.
    """
    s = job.snapshot()
    progress: int | None = None
    eta: int | None = None
    phase = "готовлю очередь…"
    if s.running and s.total > 0:
        progress = int(s.done / s.total * 100)
        phase = f"оценено {s.done} из {s.total}"
        if s.done > 0 and s.started_at is not None:
            elapsed = (dt.datetime.now(dt.UTC) - s.started_at).total_seconds()
            eta = max(5, int(elapsed / s.done * (s.total - s.done)))
    return JSONResponse(
        {
            "running": s.running,
            "done": s.done,
            "total": s.total,
            "progress": progress,
            "eta": eta,
            "phase": phase,
        }
    )
