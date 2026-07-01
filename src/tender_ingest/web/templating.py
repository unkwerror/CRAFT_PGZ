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
    """Query-строка из всех текущих фильтров с переопределениями (пагинация/сортировка)."""
    params: dict[str, object] = {
        "search": f.search,
        "exact": "1" if f.exact else None,
        "exclude": f.exclude,
        "customer": f.customer,
        "delivery": f.delivery,
        "laws": f.laws,
        "stages": f.stages,
        "verdict": f.verdict,
        "region_code": f.region_code,
        "purchase_method": f.purchase_method,
        "etp": f.etp,
        "smp_sono": f.smp_sono,
        "decided_by": f.decided_by,
        "currency": f.currency,
        "source": f.source,
        "nmck_min": f.nmck_min,
        "nmck_max": f.nmck_max,
        "nmck_none": "1" if f.nmck_none else None,
        "bid_min": f.bid_min,
        "bid_max": f.bid_max,
        "bid_none": "1" if f.bid_none else None,
        "contract_min": f.contract_min,
        "contract_max": f.contract_max,
        "contract_none": "1" if f.contract_none else None,
        "advance": f.advance,
        "score_min": f.score_min,
        "score_max": f.score_max,
        "publish_from": f.publish_from,
        "publish_to": f.publish_to,
        "deadline_from": f.deadline_from,
        "deadline_to": f.deadline_to,
        "sort": f.sort,
        "page": f.page,
    }
    params.update(overrides)
    return urlencode({k: v for k, v in params.items() if v not in (None, "", [])}, doseq=True)


templates.env.filters["money"] = money
templates.env.filters["date_ru"] = date_ru
templates.env.globals["qs"] = qs
