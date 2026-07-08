"""Трудозатраты: веб-редактор ставок ролей и факта часов (страница /labor).

Данные — база модели «себестоимость = часы × полная ставка роли». Заполнять можно
здесь построчно либо Excel-шаблоном (tender labor-template / labor-import).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from tender_ingest.db.session import get_session_factory
from tender_ingest.economics.canon import CATALOG, CATALOG_BY_KEY
from tender_ingest.economics.labor import LaborRepository, hourly_rate
from tender_ingest.web.security import require_auth
from tender_ingest.web.templating import templates

router = APIRouter(dependencies=[Depends(require_auth)])


def _back(msg: str | None = None) -> RedirectResponse:
    target = "/labor"
    if msg:
        target += f"?{urlencode({'msg': msg})}"
    return RedirectResponse(target, status_code=303)


def _dec(raw: str, default: str | None = None) -> Decimal | None:
    text = raw.replace("\xa0", "").replace(" ", "").replace(",", ".").strip()
    if not text:
        text = default or ""
    try:
        return Decimal(text) if text else None
    except InvalidOperation:
        return None


@router.get("/labor", response_class=HTMLResponse)
def labor_page(request: Request, msg: str | None = None) -> HTMLResponse:
    with get_session_factory()() as session:
        repo = LaborRepository(session)
        rates = repo.list_rates()
        hours = repo.list_hours()
        return templates.TemplateResponse(
            request,
            "labor.html",
            {
                "rates": rates,
                "hours": hours,
                "hourly": {r.id: hourly_rate(r) for r in rates},
                "roles": [r.role for r in rates],
                "catalog": CATALOG,
                "canon_labels": {k: s.label for k, s in CATALOG_BY_KEY.items()},
                "msg": msg,
            },
        )


@router.post("/labor/rates/save")
def save_rate(  # noqa: PLR0913 — плоская форма строки таблицы
    request: Request,
    rate_id: str = Form(""),
    role: str = Form(""),
    monthly_salary: str = Form(""),
    tax_coef: str = Form(""),
    overhead_coef: str = Form(""),
    fund_hours: str = Form(""),
) -> RedirectResponse:
    role_clean = role.strip()
    salary = _dec(monthly_salary)
    if not role_clean or salary is None or salary <= 0:
        return _back("Нужны роль и оклад больше нуля")
    with get_session_factory()() as session:
        LaborRepository(session).save_rate(
            int(rate_id) if rate_id.strip().isdigit() else None,
            role=role_clean,
            monthly_salary=salary,
            tax_coef=_dec(tax_coef, "1.302") or Decimal("1.302"),
            overhead_coef=_dec(overhead_coef, "1.5") or Decimal("1.5"),
            fund_hours=_dec(fund_hours, "164") or Decimal(164),
        )
    return _back(f"Ставка «{role_clean}» сохранена")


@router.post("/labor/rates/{rate_id}/delete")
def delete_rate(request: Request, rate_id: int) -> RedirectResponse:
    with get_session_factory()() as session:
        LaborRepository(session).delete_rate(rate_id)
    return _back("Ставка удалена")


@router.post("/labor/hours/save")
def save_hours(
    request: Request,
    hours_id: str = Form(""),
    project_title: str = Form(""),
    canon: str = Form(""),
    role: str = Form(""),
    hours: str = Form(""),
) -> RedirectResponse:
    title = project_title.strip()
    role_clean = role.strip()
    value = _dec(hours)
    if not title or not role_clean or value is None or value <= 0:
        return _back("Нужны проект, роль и часы больше нуля")
    with get_session_factory()() as session:
        LaborRepository(session).save_hours(
            int(hours_id) if hours_id.strip().isdigit() else None,
            project_title=title,
            canon=canon or None,
            role=role_clean,
            hours=value,
        )
    return _back("Часы сохранены")


@router.post("/labor/hours/{hours_id}/delete")
def delete_hours(request: Request, hours_id: int) -> RedirectResponse:
    with get_session_factory()() as session:
        LaborRepository(session).delete_hours(hours_id)
    return _back("Строка часов удалена")
