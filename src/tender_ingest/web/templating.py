"""Общий экземпляр Jinja2Templates + форматтеры (деньги, даты) и query-хелпер."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode

from fastapi.templating import Jinja2Templates

from tender_ingest.web.repository import Filters

_TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def money(value: object, currency: str | None = "RUB") -> str:
    """Сумма в рублях. Принимает Decimal/число/строку (из JSONB amount_rub — строка)."""
    if value is None or value == "":
        return "—"
    try:
        num = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    whole = f"{num:,.0f}".replace(",", " ")  # тысячи через обычный пробел, до целых рублей
    suffix = f" {currency}" if currency else ""
    return f"{whole}{suffix}"


def date_ru(value: dt.datetime | None, with_time: bool = False) -> str:
    if value is None:
        return "—"
    return value.strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y")


def qs(f: Filters, **overrides: object) -> str:
    """Query-строка из текущих фильтров с переопределениями (пагинация/сортировка)."""
    params: dict[str, object] = {
        "search": f.search,
        "exact": "1" if f.exact else None,
        "exclude": f.exclude,
        "verdict": f.verdict,
        "region_code": f.region_code,
        "law": f.law,
        "nmck_min": f.nmck_min,
        "nmck_max": f.nmck_max,
        "upload": f.upload,
        "sort": f.sort,
        "page": f.page,
    }
    params.update(overrides)
    return urlencode({k: v for k, v in params.items() if v not in (None, "", [])}, doseq=True)


templates.env.filters["money"] = money
templates.env.filters["date_ru"] = date_ru
templates.env.globals["qs"] = qs
