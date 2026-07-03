"""Стоп-лист заказчиков по ИНН: просмотр, добавление, удаление (управление бюро).

Заказчики из этого списка жёстко исключаются из скоринга (→ noise, без вызова Claude,
см. relevance/factors.py). Дополняет захардкоженный стоп-лист по имени (Россети/ЕЭСК).
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from tender_ingest.db.repository import BlacklistRepository
from tender_ingest.db.session import get_session_factory
from tender_ingest.web.security import require_auth
from tender_ingest.web.templating import templates

router = APIRouter(dependencies=[Depends(require_auth)])

_INN_LENGTHS = (10, 12)  # юрлицо — 10 цифр, ИП — 12


def _clean(s: str | None) -> str | None:
    return (s or "").strip() or None


def _redirect(msg: str | None = None) -> RedirectResponse:
    target = "/blacklist" + (f"?{urlencode({'msg': msg})}" if msg else "")
    return RedirectResponse(target, status_code=303)


@router.get("/blacklist", response_class=HTMLResponse)
def blacklist_page(request: Request, msg: str | None = None) -> HTMLResponse:
    with get_session_factory()() as session:
        entries = BlacklistRepository(session).list_all()
    return templates.TemplateResponse(request, "blacklist.html", {"entries": entries, "msg": msg})


@router.post("/blacklist")
def blacklist_add(
    request: Request,
    inn: Annotated[str, Form()],
    name: Annotated[str | None, Form()] = None,
    reason: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    digits = "".join(ch for ch in (inn or "") if ch.isdigit())
    if len(digits) not in _INN_LENGTHS:
        return _redirect("ИНН должен содержать 10 (юрлицо) или 12 (ИП) цифр")
    with get_session_factory()() as session:
        BlacklistRepository(session).add(digits, _clean(name), _clean(reason))
    return _redirect(f"ИНН {digits} добавлен в стоп-лист")


@router.post("/blacklist/{entry_id}/delete")
def blacklist_delete(request: Request, entry_id: int) -> RedirectResponse:
    with get_session_factory()() as session:
        BlacklistRepository(session).delete(entry_id)
    return _redirect()
