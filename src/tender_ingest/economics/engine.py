"""Детерминированный расчёт экономики тендера. Никакого LLM — только арифметика.

Принцип: ИИ (proposer) решает ТОЛЬКО семантические вопросы — какие разделы требует ТЗ,
какие исторические проекты похожи, какие накладные уместны для типа объекта. Все числа
считаются здесь: доля раздела = медиана долей по проектам-аналогам, суммы = доля × цена,
накладные клампятся в исторический диапазон (10–90 перцентиль), итог/прибыль/сетка
понижения/минимальная цена — чистая арифметика. Раздел без данных по аналогам честно
помечается no_data и в сумму не входит (никаких выдуманных цифр).

payload — единый JSON-словарь расчёта, он же хранится в tender_economics.payload
и пересчитывается при правках человека (apply_edits).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

from tender_ingest.economics.canon import CATALOG_BY_KEY
from tender_ingest.economics.store import AnalogProject

# Накладные строки бюро, присутствующие почти в каждом расчёте «Экономики».
OVERHEAD_KEYS: tuple[str, ...] = ("gip", "project_manager", "freelance_check", "reserve")

DEFAULT_REDUCTIONS: tuple[float, ...] = (5.0, 7.0, 10.0, 12.0, 15.0, 20.0, 25.0, 30.0)
DEFAULT_MIN_MARGIN_PCT = 20.0

# Кламп клампа: если истории по накладной нет, страхуемся широким разумным диапазоном.
_FALLBACK_OVERHEAD_RANGE: dict[str, tuple[float, float]] = {
    "gip": (2.0, 15.0),
    "project_manager": (1.0, 5.0),
    "freelance_check": (2.5, 7.0),
    "reserve": (3.0, 17.0),
}


@dataclass(frozen=True)
class SectionInput:
    """Раздел работ из ТЗ, сматченный proposer'ом на канонический ключ (или нет)."""

    name: str  # название как в ТЗ
    canon: str | None
    quote: str = ""
    note: str = ""


@dataclass(frozen=True)
class OverheadInput:
    """Накладная строка: процент предложен ИИ по типу проекта, будет клампнут историей."""

    canon: str
    pct: float
    rationale: str = ""


@dataclass(frozen=True)
class BaseInput:
    """База расчёта: НМЦК и решение ИИ о структуре контракта (полная цена / доля на ПД)."""

    nmck: float
    mode: str = "full"  # full | pd_share
    pd_share_pct: float | None = None
    rationale: str = ""
    quote: str = ""


@dataclass(frozen=True)
class Params:
    min_margin_pct: float = DEFAULT_MIN_MARGIN_PCT
    reductions: tuple[float, ...] = DEFAULT_REDUCTIONS


@dataclass
class _Stats:
    shares: list[float] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)


def _round2(value: float) -> float:
    return round(value, 2)


def _percentile(values: list[float], q: float) -> float:
    """Перцентиль на отсортированном списке (линейная интерполяция)."""
    data = sorted(values)
    if len(data) == 1:
        return data[0]
    pos = (len(data) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(data) - 1)
    return data[lo] + (data[hi] - data[lo]) * (pos - lo)


def _section_stats(canon: str, analogs: list[AnalogProject]) -> _Stats:
    stats = _Stats()
    for project in analogs:
        share = project.sections.get(canon)
        if share is not None and 0 < share < 0.9:
            stats.shares.append(share)
            stats.titles.append(project.title)
    return stats


def overhead_history_ranges(all_projects: list[AnalogProject]) -> dict[str, tuple[float, float]]:
    """Исторический диапазон (10–90 перцентиль долей, в %) по каждой накладной."""
    ranges: dict[str, tuple[float, float]] = {}
    for key in OVERHEAD_KEYS:
        shares = [p.sections[key] * 100 for p in all_projects if 0 < p.sections.get(key, 0.0) < 0.9]
        if len(shares) >= 5:
            ranges[key] = (_percentile(shares, 0.1), _percentile(shares, 0.9))
        else:
            ranges[key] = _FALLBACK_OVERHEAD_RANGE.get(key, (1.0, 15.0))
    return ranges


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    return max(bounds[0], min(bounds[1], value))


def build_payload(
    *,
    base: BaseInput,
    sections: list[SectionInput],
    overheads: list[OverheadInput],
    analogs: list[AnalogProject],
    all_projects: list[AnalogProject],
    analog_reasons: dict[int, str] | None = None,
    params: Params | None = None,
    comments: str = "",
) -> dict[str, Any]:
    """Собрать расчёт: строки по разделам ТЗ (медианы аналогов) + накладные + итоги."""
    p = params or Params()
    ranges = overhead_history_ranges(all_projects)
    reasons = analog_reasons or {}

    lines: list[dict[str, Any]] = []
    warnings: list[str] = []
    covered_overheads: set[str] = set()

    for section in sections:
        canon = section.canon if section.canon in CATALOG_BY_KEY else None
        entry: dict[str, Any] = {
            "name": section.name,
            "canon": canon,
            "canon_label": CATALOG_BY_KEY[canon].label if canon else None,
            "group": CATALOG_BY_KEY[canon].group if canon else None,
            "quote": section.quote,
            "note": section.note,
            "share_pct": None,
            "amount": None,
            "source": "no_data",
            "n_analogs": 0,
            "range_pct": None,
            "analog_titles": [],
        }
        if canon is not None:
            stats = _section_stats(canon, analogs)
            if stats.shares:
                share = statistics.median(stats.shares)
                entry.update(
                    {
                        "share_pct": round(share * 100, 2),
                        "amount": _round2(share * base.nmck),
                        "source": "analog",
                        "n_analogs": len(stats.shares),
                        "range_pct": [
                            round(min(stats.shares) * 100, 2),
                            round(max(stats.shares) * 100, 2),
                        ],
                        "analog_titles": stats.titles[:5],
                    }
                )
                if canon in OVERHEAD_KEYS:
                    covered_overheads.add(canon)
        lines.append(entry)

    overhead_lines: list[dict[str, Any]] = []
    for overhead in overheads:
        if overhead.canon not in OVERHEAD_KEYS or overhead.canon in covered_overheads:
            continue
        bounds = ranges[overhead.canon]
        pct = _clamp(overhead.pct, bounds)
        if pct != overhead.pct:
            warnings.append(
                f"Накладная «{CATALOG_BY_KEY[overhead.canon].label}»: предложение ИИ "
                f"{overhead.pct:.1f}% вне исторического диапазона — ограничено до {pct:.1f}%."
            )
        overhead_lines.append(
            {
                "name": CATALOG_BY_KEY[overhead.canon].label,
                "canon": overhead.canon,
                "canon_label": CATALOG_BY_KEY[overhead.canon].label,
                "group": "overhead",
                "quote": "",
                "note": overhead.rationale,
                "share_pct": round(pct, 2),
                "amount": _round2(pct / 100 * base.nmck),
                "source": "ai",
                "n_analogs": 0,
                "range_pct": [round(bounds[0], 2), round(bounds[1], 2)],
                "analog_titles": [],
            }
        )

    payload: dict[str, Any] = {
        "base": {
            "nmck": _round2(base.nmck),
            "mode": base.mode,
            "pd_share_pct": base.pd_share_pct,
            "rationale": base.rationale,
            "quote": base.quote,
        },
        "params": {"min_margin_pct": p.min_margin_pct, "reductions": list(p.reductions)},
        "lines": lines,
        "overheads": overhead_lines,
        "analogs": [
            {
                "id": a.id,
                "title": a.title,
                "sheet": a.sheet,
                "contract_total": a.contract_total,
                "reason": reasons.get(a.id, ""),
            }
            for a in analogs
        ],
        "comments": comments,
        "warnings_static": warnings,  # кламп накладных и пр. — не зависят от пересчёта
    }
    _recompute_totals(payload)
    return payload


def apply_edits(
    payload: dict[str, Any],
    line_edits: dict[str, dict[str, float | None]],
    min_margin_pct: float | None = None,
) -> dict[str, Any]:
    """Правки человека: {'l0': {'amount': …} | {'share_pct': …}, 'o1': …} -> новый payload.

    Ключ — префикс l (lines) / o (overheads) + индекс. Задан amount — доля выводится из
    него; задана share_pct — сумма из доли. Правленая строка помечается source='user'.
    """
    result: dict[str, Any] = dict(payload)
    result["lines"] = [dict(line) for line in payload.get("lines", [])]
    result["overheads"] = [dict(line) for line in payload.get("overheads", [])]
    result["params"] = dict(payload.get("params", {}))
    base_data = result.get("base")
    nmck = float(base_data["nmck"]) if isinstance(base_data, dict) else 0.0

    for key, edit in line_edits.items():
        bucket = result["lines"] if key.startswith("l") else result["overheads"]
        try:
            idx = int(key[1:])
            line = bucket[idx]
        except (ValueError, IndexError):
            continue
        amount = edit.get("amount")
        share_pct = edit.get("share_pct")
        if amount is not None:
            line["amount"] = _round2(amount)
            line["share_pct"] = round(amount / nmck * 100, 2) if nmck else None
        elif share_pct is not None:
            line["share_pct"] = round(share_pct, 2)
            line["amount"] = _round2(share_pct / 100 * nmck)
        else:
            continue
        line["source"] = "user"

    if min_margin_pct is not None:
        result["params"]["min_margin_pct"] = min_margin_pct
    _recompute_totals(result)
    return result


def _recompute_totals(payload: dict[str, Any]) -> None:
    """Итоги, сетка понижения и минимальная цена — из строк payload (in-place)."""
    base = payload["base"]
    nmck = float(base["nmck"])
    all_lines = list(payload["lines"]) + list(payload["overheads"])
    cost = sum(float(line["amount"]) for line in all_lines if line.get("amount") is not None)
    no_data = [line["name"] for line in payload["lines"] if line.get("amount") is None]

    profit = nmck - cost
    payload["totals"] = {
        "cost": _round2(cost),
        "profit_at_nmck": _round2(profit),
        "margin_pct": round(profit / nmck * 100, 1) if nmck else None,
    }
    payload["no_data"] = no_data

    reductions = [float(r) for r in payload["params"].get("reductions", DEFAULT_REDUCTIONS)]
    payload["scenarios"] = [
        {
            "reduction_pct": r,
            "price": _round2(nmck * (1 - r / 100)),
            "profit": _round2(nmck * (1 - r / 100) - cost),
            "margin_pct": round((nmck * (1 - r / 100) - cost) / nmck * 100, 1) if nmck else None,
        }
        for r in reductions
    ]

    min_margin = float(payload["params"].get("min_margin_pct", DEFAULT_MIN_MARGIN_PCT))
    min_price = cost * (1 + min_margin / 100)
    max_reduction = (nmck - min_price) / nmck * 100 if nmck else 0.0
    payload["min_price"] = {
        "price": _round2(min_price),
        "min_margin_pct": min_margin,
        "max_reduction_pct": round(max_reduction, 1),
    }

    warnings: list[str] = []
    if nmck and cost > nmck:
        warnings.append(
            "⚠ Себестоимость ВЫШЕ НМЦК: участие убыточно при текущей цене "
            f"(себестоимость {cost:,.0f} ₽ против НМЦК {nmck:,.0f} ₽).".replace(",", " ")
        )
    elif nmck and min_price > nmck:
        warnings.append(
            "⚠ Даже без снижения цены маржа ниже целевой "
            f"({payload['totals']['margin_pct']}% при целевых {min_margin:.0f}%)."
        )
    if no_data:
        warnings.append(
            "Разделы без данных по аналогам (в сумме НЕ учтены, нужна ручная оценка): "
            + "; ".join(no_data)
        )
    payload["warnings"] = warnings + list(payload.get("warnings_static", []))
