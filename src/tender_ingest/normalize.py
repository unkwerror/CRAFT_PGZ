"""Нормализация значений из выгрузки Контура (CLAUDE.md, раздел 3 «Подводные камни»).

Чистые функции без побочных эффектов — их легко покрыть тестами на фикстуре.
Excel читается через openpyxl (`data_only=True`), поэтому даты обычно приходят уже
как `datetime`; но guard по Excel-serial оставлен на случай, когда значение придёт
числом (другой ридер/формат ячейки).
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation
from typing import NamedTuple

# База Excel-serial: 1899-12-30 (учитывает «баг 1900 года» Lotus/Excel).
_EXCEL_EPOCH = dt.datetime(1899, 12, 30)
# Разумный диапазон serial -> примерно 1982-01-01 .. 2064-03-12.
_SERIAL_MIN = 30000
_SERIAL_MAX = 60000

_REGION_RE = re.compile(r"^\s*(\d{1,2})\s+(.+?)\s*$")
_PERCENT_RE = re.compile(r"^\s*([\d\s.,]+)\s*%\s*$")


def clean_str(value: object) -> str | None:
    """Пустые ячейки openpyxl приходят как ''/None/пробелы -> None; иначе trimmed str."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_number(value: object) -> str | None:
    """Реестровый номер — ВСЕГДА строка (ведущие нули, буквенные ID). Не приводить к int."""
    return clean_str(value)


def parse_money(value: object) -> Decimal | None:
    """НМЦ/сумма в рублях -> Decimal. Строки с пробелами-разделителями тоже понимает."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = clean_str(value)
    if text is None:
        return None
    text = text.replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_date(value: object) -> dt.datetime | None:
    """datetime -> как есть; число -> Excel-serial с guard; строка -> ISO-парс; иначе None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day)
    if isinstance(value, (int, float)):
        if not (_SERIAL_MIN <= value <= _SERIAL_MAX):
            return None
        return _EXCEL_EPOCH + dt.timedelta(days=float(value))
    text = clean_str(value)
    if text is None:
        return None
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        return None


class Region(NamedTuple):
    code: str | None
    name: str | None


def split_region(value: object) -> Region:
    """`'77 Москва'` -> ('77', 'Москва'); `'01 Республика Адыгея'` -> ('01', ...)."""
    text = clean_str(value)
    if text is None:
        return Region(None, None)
    m = _REGION_RE.match(text)
    if m:
        return Region(m.group(1), m.group(2))
    return Region(None, text)


class Security(NamedTuple):
    """Обеспечение/аванс: сырое значение + распарсенное (рубли ИЛИ процент)."""

    raw: str | None
    amount_rub: Decimal | None
    percent: Decimal | None


def parse_security(value: object) -> Security:
    """`164799.27` -> рубли; `'30.00 %'` -> процент; `'Есть. См. документацию'` -> только raw."""
    if value is None:
        return Security(None, None, None)
    if isinstance(value, bool):
        return Security(str(value), None, None)
    if isinstance(value, (int, float)):
        return Security(str(value), Decimal(str(value)), None)
    text = clean_str(value)
    if text is None:
        return Security(None, None, None)
    m = _PERCENT_RE.match(text)
    if m:
        pct = m.group(1).replace("\xa0", "").replace(" ", "").replace(",", ".")
        try:
            return Security(text, None, Decimal(pct))
        except InvalidOperation:
            return Security(text, None, None)
    money = parse_money(text)
    return Security(text, money, None)
