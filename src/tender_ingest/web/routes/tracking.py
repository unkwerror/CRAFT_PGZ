"""Избранное, участие в торгах и заметки бюро — «человеческий» слой RAG (фаза A).

Всё вводится с карточки тендера. Данные немедленно попадают в корпус кейсов
ИИ-экономиста и в аналитику — без переиндексации (см. docs/analytics-brief.md).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from tender_ingest.db.session import get_session_factory
from tender_ingest.web.security import require_auth
from tender_ingest.web.tracking import PARTICIPATION_STATUSES, TrackingRepository

router = APIRouter(dependencies=[Depends(require_auth)])


def _detail(reestr_number: str, msg: str | None = None) -> RedirectResponse:
    target = f"/tender/{reestr_number}"
    if msg:
        target += f"?{urlencode({'msg': msg})}"
    return RedirectResponse(target, status_code=303)


def _clean(s: str | None) -> str | None:
    return (s or "").strip() or None


def _price(s: str | None) -> Decimal | None:
    text = _clean(s)
    if text is None:
        return None
    try:
        return Decimal(text.replace(" ", "").replace(" ", "").replace(",", "."))
    except InvalidOperation:
        return None


def _date(s: str | None) -> dt.date | None:
    text = _clean(s)
    if text is None:
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


@router.post("/tender/{reestr_number}/favorite")
def toggle_favorite(request: Request, reestr_number: str) -> RedirectResponse:
    with get_session_factory()() as session:
        now_fav = TrackingRepository(session).toggle_favorite(reestr_number)
    return _detail(reestr_number, "Добавлено в избранное" if now_fav else "Убрано из избранного")


@router.post("/tender/{reestr_number}/participation")
def save_participation(
    request: Request,
    reestr_number: str,
    status: Annotated[str, Form()] = "",
    our_price: Annotated[str | None, Form()] = None,
    winner_price: Annotated[str | None, Form()] = None,
    decided_at: Annotated[str | None, Form()] = None,
    comment: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """Сохранить исход участия. Пустой статус — удалить запись (не участвуем)."""
    status = (status or "").strip()
    with get_session_factory()() as session:
        repo = TrackingRepository(session)
        if not status:
            repo.delete_participation(reestr_number)
            return _detail(reestr_number, "Отметка об участии снята")
        if status not in PARTICIPATION_STATUSES:
            return _detail(reestr_number, "Неизвестный статус участия")
        repo.upsert_participation(
            reestr_number,
            status=status,
            our_price=_price(our_price),
            winner_price=_price(winner_price),
            decided_at=_date(decided_at),
            comment=_clean(comment),
        )
    return _detail(reestr_number, "Участие сохранено — данные попадут в аналитику и RAG")


@router.post("/tender/{reestr_number}/notes")
def add_note(
    request: Request,
    reestr_number: str,
    text: Annotated[str, Form()] = "",
) -> RedirectResponse:
    cleaned = _clean(text)
    if cleaned is None:
        return _detail(reestr_number, "Пустая заметка не сохранена")
    with get_session_factory()() as session:
        TrackingRepository(session).add_note(reestr_number, cleaned)
    return _detail(reestr_number, "Заметка сохранена")


@router.post("/tender/{reestr_number}/notes/{note_id}/delete")
def delete_note(request: Request, reestr_number: str, note_id: int) -> RedirectResponse:
    with get_session_factory()() as session:
        TrackingRepository(session).delete_note(reestr_number, note_id)
    return _detail(reestr_number)
