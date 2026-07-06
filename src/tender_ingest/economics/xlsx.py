"""Парсер таблицы «Экономика (2).xlsx» — база знаний расчётов бюро.

Оба листа («В РАБОТЕ» — план/факт закрытых и текущих проектов, «ПРЕДВАРИТЕЛЬНЫЕ
проекты» — прикидки под тендеры) устроены блоками: 1–2 строки названия, строка
«Всего согласно договора» (цена, иногда пометка «(40% на ПД)»), строки разделов
(доля в колонке C и/или суммы план/факт в D/E, исполнитель в F), «ИТОГО», «Прибыль»
и мета-строки (дедлайн, гарантии, комментарии ГИПа/юриста).

Ключевая устойчивость: доля раздела считается как план/договор (share), а не берётся
из колонки C — в части блоков проценты там от «п.3» (бюджета разработки), не от цены.
Сетку «% понижения» справа не читаем — считаем сами в engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

import openpyxl

from tender_ingest.economics.canon import match_canon, normalize_name


@dataclass
class ParsedLine:
    position: int
    name_raw: str
    canon: str | None
    pct: Decimal | None  # значение колонки «%», как в файле (доля 0..1)
    planned: Decimal | None
    fact: Decimal | None
    fact_raw: str | None
    comment: str | None
    share: Decimal | None  # план / цена договора — устойчивая доля от цены


@dataclass
class ParsedProject:
    sheet: str  # 'work' | 'preliminary'
    position: int
    title: str
    contract_total: Decimal | None
    contract_note: str | None
    cost_planned: Decimal | None = None
    cost_fact: Decimal | None = None
    profit: Decimal | None = None
    meta: dict[str, str] = field(default_factory=dict)
    lines: list[ParsedLine] = field(default_factory=list)


_SHEET_KIND = {0: "work", 1: "preliminary"}

_CONTRACT_PREFIX = "всего согласно договора"
_TOTAL_PREFIX = "итого"
_PROFIT_PREFIX = "прибыль"

# Мета-строки блока: сохраняем в meta {название: значение}.
# «Обеспечение…» — только заявка/контракт/договор: «Обеспечение сохранности ОКН» — затрата.
_META_PREFIXES = (
    "окончание",
    "критерии подачи",
    "банковская гарантия",
    "комментари",
    "требование к участникам",
    "авансирование",
    "срок",
    "ссылка на",
    "обеспечение заявки",
    "обеспечение исполнения",
    "обеспечение контракта",
    "обеспечение договора",
    "обеспечение гарантийных",
    "публикация протокола",
)

# Структурные/финансовые строки — не затраты по разделам, пропускаем.
_SKIP_PREFIXES = (
    "том/раздел",
    "остаток с учетом",
    "всего расходы",
    "затраты на разработку",
    "затраты на пд",
    "расчет пени",
    "цена контракта",
    "на разработку",
    "на проектирование и прибыль",
    "дополнительные разделы",
    "гарантийные обязательства",
    "начальная стоимость",
    "единый налог",
    "ндс",
    "стоимость договора",
    "прочие расходы",
    "расходы на привлечение",
    "согласование документации заказчиком",
)

_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def _to_decimal(value: object) -> Decimal | None:
    """Число из ячейки: float/int или строка вида «56 317,80». Иначе None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    text = str(value).replace("\xa0", "").replace(" ", "").replace(",", ".")
    text = text.removesuffix("%").strip()
    if not _NUM_RE.match(text):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _starts_with(name_norm: str, prefixes: tuple[str, ...]) -> bool:
    return any(name_norm.startswith(p) for p in prefixes)


def _meta_value(cells: tuple[object, ...]) -> str:
    parts = [_cell_text(c) for c in cells[2:7]]
    return "; ".join(p for p in parts if p)


def _round2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def parse_workbook(path: Path) -> list[ParsedProject]:
    """Разобрать оба листа на блоки проектов. Порядок листов: активный первым не важен —
    берём по индексу: 0 — «В РАБОТЕ», 1 — «ПРЕДВАРИТЕЛЬНЫЕ» (имена не хардкодим)."""
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    projects: list[ParsedProject] = []
    for sheet_idx, ws in enumerate(wb.worksheets[:2]):
        kind = _SHEET_KIND[sheet_idx]
        current: ParsedProject | None = None
        title_buf: list[str] = []
        for row in ws.iter_rows(values_only=True):
            cells = tuple(row) + (None,) * (7 - len(row))
            col0, name = _cell_text(cells[0]), _cell_text(cells[1])

            if not name:
                # Кандидат в название блока: текст только в первой колонке.
                if col0 and _to_decimal(cells[0]) is None:
                    title_buf.append(col0)
                continue

            name_norm = normalize_name(name)

            if name_norm.startswith(_CONTRACT_PREFIX):
                current = ParsedProject(
                    sheet=kind,
                    position=len(projects),
                    title=" / ".join(title_buf[-2:]) or "(без названия)",
                    contract_total=_to_decimal(cells[3]),
                    contract_note=_cell_text(cells[2]) or None,
                )
                projects.append(current)
                title_buf = []
                continue

            if current is None:
                continue

            if name_norm.startswith(_TOTAL_PREFIX):
                # «Итого ОСТАТОК (за вычетом …)» — не себестоимость, пропускаем.
                if "остаток" not in name_norm:
                    current.cost_planned = _to_decimal(cells[3])
                    current.cost_fact = _to_decimal(cells[4])
                continue
            if name_norm.startswith(_PROFIT_PREFIX):
                current.profit = _to_decimal(cells[3]) or _to_decimal(cells[4])
                continue
            if _starts_with(name_norm, _META_PREFIXES):
                value = _meta_value(cells)
                if value:
                    current.meta[name] = value
                continue
            if _starts_with(name_norm, _SKIP_PREFIXES):
                continue

            pct = _to_decimal(cells[2])
            planned = _to_decimal(cells[3])
            fact = _to_decimal(cells[4])
            fact_raw = _cell_text(cells[4]) or None
            comment = _cell_text(cells[5]) or None

            if pct is None and planned is None and fact is None and not col0:
                # Продолжение имени предыдущей строки («Подраздел 1 …» на своей строке).
                if current.lines:
                    prev = current.lines[-1]
                    prev.name_raw = f"{prev.name_raw} {name}"
                    prev.canon = match_canon(prev.name_raw)
                continue

            share: Decimal | None = None
            if planned is not None and current.contract_total:
                share = planned / current.contract_total
                if not Decimal("0") <= share <= Decimal("1.5"):
                    share = None  # мусор/опечатка — долю не доверяем

            current.lines.append(
                ParsedLine(
                    position=len(current.lines),
                    name_raw=name,
                    canon=match_canon(name),
                    pct=pct if pct is not None and Decimal("0") < pct <= Decimal("1.5") else None,
                    planned=_round2(planned) if planned is not None else None,
                    fact=_round2(fact) if fact is not None else None,
                    fact_raw=fact_raw,
                    comment=comment,
                    share=share.quantize(Decimal("0.000001")) if share is not None else None,
                )
            )
    wb.close()
    return projects
