"""Закрытые тендеры: ручное добавление закупок не с Контура (source='closed').

Карточки Контура нет — минимальная форма (номер/название/НМЦК/дедлайн опциональны)
+ файл ТЗ. Разбор ТЗ дополнительно извлекает поля карточки (см. doc_analysis_job:
заполняются только пустые, ручной ввод приоритетен), после чего тендер попадает
в очередь скоринга. Дальше функционал общий: бриф, скоринг, экономика.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from tender_ingest.config import get_settings
from tender_ingest.db.models import Tender
from tender_ingest.db.session import get_session_factory
from tender_ingest.web.doc_analysis_job import CLOSED_SOURCE
from tender_ingest.web.doc_analysis_job import job as doc_job
from tender_ingest.web.repository import DocumentRepository
from tender_ingest.web.security import require_auth
from tender_ingest.web.templating import templates

router = APIRouter(dependencies=[Depends(require_auth)])


def _generate_number(session_factory: object) -> str:
    """CLOSED-<год>-<порядковый №>: следующий свободный номер в текущем году."""
    year = dt.date.today().year
    prefix = f"CLOSED-{year}-"
    with get_session_factory()() as session:
        count = session.execute(
            select(func.count()).select_from(Tender).where(Tender.reestr_number.like(prefix + "%"))
        ).scalar_one()
    return f"{prefix}{count + 1:04d}"


def _to_decimal(raw: str | None) -> Decimal | None:
    text = (raw or "").replace("\xa0", "").replace(" ", "").replace(",", ".").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


@router.get("/closed/new", response_class=HTMLResponse)
def new_closed_form(request: Request, error: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(request, "closed_new.html", {"error": error})


@router.post("/closed/new")
def create_closed(  # noqa: PLR0913 — плоские поля формы
    request: Request,
    reestr_number: Annotated[str, Form()] = "",
    subject: Annotated[str, Form()] = "",
    nmck: Annotated[str, Form()] = "",
    submission_deadline: Annotated[str, Form()] = "",
    file: Annotated[UploadFile | None, File()] = None,
) -> RedirectResponse:
    """Создать закрытый тендер; при приложенном ТЗ — сразу запустить разбор."""
    number = re.sub(r"\s+", "", reestr_number) or _generate_number(get_session_factory)
    deadline: dt.datetime | None = None
    if submission_deadline.strip():
        try:
            deadline = dt.datetime.fromisoformat(submission_deadline.strip())
        except ValueError:
            deadline = None

    with get_session_factory()() as session:
        if session.get(Tender, number) is not None:
            query = urlencode({"error": f"Тендер с номером {number} уже существует"})
            return RedirectResponse(f"/closed/new?{query}", status_code=303)
        price = _to_decimal(nmck)
        session.add(
            Tender(
                reestr_number=number,
                source=CLOSED_SOURCE,
                subject=subject.strip() or None,
                nmck=price,
                currency="RUB" if price is not None else None,
                submission_deadline=deadline,
            )
        )
        session.commit()

        doc_id: int | None = None
        name = (file.filename or "").strip() if file is not None else ""
        if file is not None and name:
            data = file.file.read()
            limit = get_settings().doc_max_mb * 1024 * 1024
            if data and len(data) <= limit:
                DocumentRepository(session).add(number, name, file.content_type, data)
                docs = DocumentRepository(session).list_for(number)
                doc_id = docs[0].id if docs else None

    msg = f"Закрытый тендер {number} создан"
    if doc_id is not None:
        if doc_job.start(doc_id):
            msg += ". Разбор ТЗ запущен — ИИ заполнит карточку из документа"
        else:
            msg += ". ТЗ загружено; запустите разбор, когда освободится ИИ"
    else:
        msg += ". Загрузите ТЗ и нажмите «Разобрать ТЗ» — ИИ заполнит карточку"
    return RedirectResponse(f"/tender/{number}?{urlencode({'msg': msg})}", status_code=303)
