"""Общий интерфейс источника и нормализованный объект закупки (CLAUDE.md, раздел 4).

Pipeline работает ТОЛЬКО с `RawTender` и ничего не знает про конкретный источник.
Это шов, чтобы позже подключить EmailSource/DamiaSource/EisSource, не переписывая
остальное.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from collections.abc import Iterable
from decimal import Decimal

from pydantic import BaseModel, Field


class SecurityValue(BaseModel):
    """Одно обеспечение/аванс: сырое значение + распарсенные рубли/процент."""

    raw: str | None = None
    amount_rub: Decimal | None = None
    percent: Decimal | None = None


class TenderResult(BaseModel):
    """Итог закупки (обычно пусто, пока идёт приём заявок)."""

    protocol_date: dt.datetime | None = None
    winner_name: str | None = None
    winner_inn: str | None = None
    winner_kpp: str | None = None
    winner_offer: str | None = None


class RawTender(BaseModel):
    """Нормализованная закупка из любого источника. `raw` хранит исходную строку."""

    reestr_number: str
    source: str

    # Закупка
    subject: str | None = None
    nmck: Decimal | None = None
    currency: str | None = None
    law: str | None = None  # 44-ФЗ | 223-ФЗ | Коммерческие | 615 ПП
    purchase_method: str | None = None
    stage: str | None = None
    etp: str | None = None
    smp_sono: str | None = None
    publish_date: dt.datetime | None = None
    submission_deadline: dt.datetime | None = None
    delivery_place: str | None = None

    # Обеспечения (5 видов) + аванс
    securities: dict[str, SecurityValue] = Field(default_factory=dict)
    advance_raw: str | None = None
    advance_pct: Decimal | None = None

    # Заказчик
    customer_name: str | None = None
    customer_inn: str | None = None
    customer_kpp: str | None = None
    region_code: str | None = None
    region_name: str | None = None

    # Результат
    result: TenderResult = Field(default_factory=TenderResult)

    # Сырьё строки целиком (ключи — заголовки колонок) для tender_raw.payload
    raw: dict[str, object] = Field(default_factory=dict)


class SourceAdapter(ABC):
    """Источник закупок. Реализации: ExcelSource (v1), позже Email/Damia/Eis."""

    #: машиночитаемое имя источника, попадает в `RawTender.source`
    name: str

    @abstractmethod
    def fetch(self) -> Iterable[RawTender]:
        """Вернуть поток нормализованных закупок из источника."""
        raise NotImplementedError
