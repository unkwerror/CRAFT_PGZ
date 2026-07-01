"""Объективные факторы карточки закупки + жёсткие исключения (детерминированно, из полей).

Что можно однозначно посчитать из структурированных полей — считаем здесь (дёшево,
без LLM). Жёсткие дисквалификаторы отсекаем ДО Claude, экономя вызовы. Семантику
(профиль/отрасль, дорожные работы) оставляем Claude — см. arbiter/prompt.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

# Приоритетные регионы: Уральский ФО + Пермский край + Омская область (коды субъектов).
# 45 Курганская, 66 Свердловская, 72 Тюменская, 74 Челябинская, 86 ХМАО-Югра,
# 89 ЯНАО, 59 Пермский край, 55 Омская область.
PRIORITY_REGIONS = frozenset({"45", "66", "72", "74", "86", "89", "59", "55"})

NMCK_MIN = Decimal("2000000")  # 2 млн ₽ — нижняя граница «в диапазоне»
NMCK_MAX = Decimal("150000000")  # 150 млн ₽ — верхняя граница «в диапазоне»
# Жёсткие границы: вне — исключаем; в зоне затухания (1.5–2 / 150–180) Claude штрафует.
NMCK_HARD_MIN = Decimal("1500000")  # 1.5 млн ₽
NMCK_HARD_MAX = Decimal("180000000")  # 180 млн ₽

_AUCTION = re.compile(r"аукцион", re.IGNORECASE)
_KONKURS = re.compile(r"конкурс", re.IGNORECASE)
# Стоп-лист заказчиков: любые «Россети», «ЕЭСК» / «Екатеринбургская электросетевая».
_EXCLUDED_CUSTOMER = re.compile(r"россети|еэск|екатеринбургская\s+электросетевая", re.IGNORECASE)


@dataclass
class Factors:
    law: str | None
    method_kind: str | None  # "конкурс" | "аукцион" | "иное" | None
    has_advance: bool
    nmck: Decimal | None
    nmck_in_band: bool  # 2–150 млн ₽
    region_code: str | None
    region_priority: bool
    customer_excluded: bool
    stage: str | None
    stage_active: bool  # этап «Подача заявок»

    def as_dict(self) -> dict[str, object]:
        return {
            "law": self.law,
            "method_kind": self.method_kind,
            "has_advance": self.has_advance,
            "nmck": str(self.nmck) if self.nmck is not None else None,
            "nmck_in_band": self.nmck_in_band,
            "region_code": self.region_code,
            "region_priority": self.region_priority,
            "customer_excluded": self.customer_excluded,
            "stage": self.stage,
            "stage_active": self.stage_active,
        }


class _CardLike(Protocol):
    """Структурный протокол: у объекта есть поля закупки (SQLAlchemy Tender подходит)."""

    law: str | None
    purchase_method: str | None
    advance_raw: str | None
    nmck: Decimal | None
    region_code: str | None
    customer_name: str | None
    stage: str | None


def compute_factors(t: _CardLike) -> Factors:
    method = t.purchase_method or ""
    if _AUCTION.search(method):
        kind: str | None = "аукцион"
    elif _KONKURS.search(method):
        kind = "конкурс"
    elif method.strip():
        kind = "иное"
    else:
        kind = None

    nmck = t.nmck
    return Factors(
        law=t.law,
        method_kind=kind,
        has_advance=t.advance_raw is not None,
        nmck=nmck,
        nmck_in_band=(nmck is not None and NMCK_MIN <= nmck <= NMCK_MAX),
        region_code=t.region_code,
        region_priority=(t.region_code in PRIORITY_REGIONS),
        # Стоп-лист по имени — быстрый пре-фильтр; при появлении таблицы известных
        # ИНН матчить по ИНН как основной признак (см. FIXES/analysis).
        customer_excluded=bool(_EXCLUDED_CUSTOMER.search(t.customer_name or "")),
        stage=t.stage,
        stage_active=((t.stage or "").strip().lower() == "подача заявок"),
    )


def hard_exclusion(f: Factors) -> str | None:
    """Причина жёсткого исключения (→ noise, без вызова Claude) или None.

    СНГ/зарубеж определяет Claude по промпту (в полях выгрузки надёжного признака нет).
    Аукцион — не жёсткое исключение, а мягкое (см. is_auction).
    """
    if f.customer_excluded:
        return "заказчик в стоп-листе (Россети / ЕЭСК)"
    if f.stage is not None and not f.stage_active:
        return f"приём заявок неактивен (этап: {f.stage})"
    if f.nmck is not None and (f.nmck < NMCK_HARD_MIN or f.nmck > NMCK_HARD_MAX):
        return "НМЦ вне рабочего диапазона (1.5–180 млн ₽)"
    return None


def is_auction(f: Factors) -> bool:
    """Электронный аукцион — мягко откладываем (отдельный список), не хороним в шум."""
    return f.method_kind == "аукцион"
