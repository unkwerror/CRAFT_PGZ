"""Детерминированный расчёт экономики тендера. Никакого LLM — только арифметика.

Принцип: ИИ (proposer) решает ТОЛЬКО семантические вопросы — какие разделы требует ТЗ,
какие исторические проекты похожи, какие накладные уместны для типа объекта. Все числа
считаются здесь. Раздел без данных по аналогам сначала пробует нормативный вес СБЦП
(только здания), иначе честно помечается no_data и в сумму не входит.

Два режима базы:
- НМЦК известна: доля раздела = медиана долей по аналогам, сумма = доля × НМЦК,
  сетка понижения цены, минимально допустимая цена.
- НМЦК НЕТ (закрытый тендер без цены): формируем ПРЕДЛОЖЕНИЕ компании от себестоимости:
  сумма раздела = медиана АБСОЛЮТНЫХ затрат по аналогам (₽), накладные — процент от
  итоговой цены, цена = себестоимость_разделов / (1 − накладные% − маржа%). Маржа по
  умолчанию — медиана прибыли бюро по базе «Экономики», сетка вариантов по маржам.

payload — единый JSON-словарь расчёта, он же хранится в tender_economics.payload
и пересчитывается при правках человека (apply_edits).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

from tender_ingest.economics.canon import CATALOG_BY_KEY
from tender_ingest.economics.sbcp import SBCP_SOURCE, sbcp_weights, stage_label
from tender_ingest.economics.store import AnalogProject

# Накладные строки бюро, присутствующие почти в каждом расчёте «Экономики».
OVERHEAD_KEYS: tuple[str, ...] = ("gip", "project_manager", "freelance_check", "reserve")

DEFAULT_REDUCTIONS: tuple[float, ...] = (5.0, 7.0, 10.0, 12.0, 15.0, 20.0, 25.0, 30.0)
DEFAULT_OFFER_MARGINS: tuple[float, ...] = (20.0, 30.0, 40.0, 50.0)
DEFAULT_MIN_MARGIN_PCT = 20.0
DEFAULT_TARGET_MARGIN_PCT = 35.0  # если медиану маржи бюро посчитать не удалось

# Кламп клампа: если истории по накладной нет, страхуемся широким разумным диапазоном.
_FALLBACK_OVERHEAD_RANGE: dict[str, tuple[float, float]] = {
    "gip": (2.0, 15.0),
    "project_manager": (1.0, 5.0),
    "freelance_check": (2.5, 7.0),
    "reserve": (3.0, 17.0),
}

# Знаменатель формулы цены предложения не даём упасть ниже этого значения:
# накладные + маржа не могут съесть больше 90% цены.
_MIN_OFFER_DENOMINATOR = 0.1


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
    """База расчёта: НМЦК (или её отсутствие -> режим предложения цены) и структура."""

    nmck: float | None
    mode: str = "full"  # full | pd_share | offer (без НМЦК)
    pd_share_pct: float | None = None
    rationale: str = ""
    quote: str = ""


@dataclass(frozen=True)
class Params:
    min_margin_pct: float = DEFAULT_MIN_MARGIN_PCT
    target_margin_pct: float = DEFAULT_TARGET_MARGIN_PCT  # для режима предложения
    reductions: tuple[float, ...] = DEFAULT_REDUCTIONS
    offer_margins: tuple[float, ...] = DEFAULT_OFFER_MARGINS


@dataclass
class _Stats:
    values: list[float] = field(default_factory=list)  # доли (0..1) или суммы (₽)
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


def _section_stats(canon: str, analogs: list[AnalogProject], *, absolute: bool) -> _Stats:
    """Статистика раздела по аналогам: доли от цены (absolute=False) или суммы в ₽."""
    stats = _Stats()
    for project in analogs:
        if absolute:
            value = project.amounts.get(canon)
            if value is not None and value > 0:
                stats.values.append(value)
                stats.titles.append(project.title)
        else:
            share = project.sections.get(canon)
            if share is not None and 0 < share < 0.9:
                stats.values.append(share)
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


def _apply_sbcp_fallback(
    lines: list[dict[str, Any]],
    *,
    nmck: float | None,
    object_kind: str,
    design_stage: str,
    warnings: list[str],
) -> None:
    """Второй источник: нормативные веса СБЦП для разделов без аналогов (только здания).

    Веса СБЦП относительные — приводятся к уровню затрат бюро коэффициентом
    k = медиана(значение_по_аналогам / вес_СБЦП) по разделам-«якорям» этого же расчёта.
    С НМЦК якорь — доля от цены; без НМЦК — абсолютная сумма (пропорция сумм).
    Якорей меньше двух — фолбэк честно отключается (никакой подгонки).
    """
    if object_kind != "building":
        return
    weights = sbcp_weights(design_stage)
    targets = [
        line for line in lines if line["source"] == "no_data" and line.get("canon") in weights
    ]
    if not targets:
        return

    def anchor_value(line: dict[str, Any]) -> float | None:
        if nmck is not None:
            share = line.get("share_pct")
            return float(share) / 100.0 if share is not None else None
        amount = line.get("amount")
        return float(amount) if amount is not None else None

    anchors = [
        (value, weights[canon])
        for line in lines
        if line["source"] == "analog"
        and (canon := line.get("canon")) in weights
        and (value := anchor_value(line)) is not None
    ]
    if len(anchors) < 2:
        warnings.append(
            "СБЦП не применён: мало разделов-якорей (нужно ≥2 раздела, у которых есть "
            "и аналоги бюро, и нормативный вес) — разделы без данных оценивайте вручную."
        )
        return
    k = statistics.median(value / weight for value, weight in anchors)
    for line in targets:
        scaled = weights[str(line["canon"])] * k
        extra = f" · {line['note']}" if line.get("note") else ""
        note = (
            f"вес по СБЦП (стадия {stage_label(design_stage)}), приведён к уровню "
            f"затрат бюро (k по {len(anchors)} разделам-якорям). {SBCP_SOURCE}{extra}"
        )
        if nmck is not None:
            line.update(
                {
                    "share_pct": round(scaled * 100, 2),
                    "amount": _round2(scaled * nmck),
                    "source": "sbcp",
                    "note": note,
                }
            )
        else:
            line.update({"amount": _round2(scaled), "source": "sbcp", "note": note})


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
    object_kind: str = "other",
    design_stage: str = "pd_rd",
) -> dict[str, Any]:
    """Собрать расчёт: строки по разделам ТЗ (медианы аналогов, фолбэк СБЦП) + накладные."""
    p = params or Params()
    offer_mode = base.nmck is None
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
            stats = _section_stats(canon, analogs, absolute=offer_mode)
            if stats.values:
                median = statistics.median(stats.values)
                entry.update(
                    {
                        "source": "analog",
                        "n_analogs": len(stats.values),
                        "analog_titles": stats.titles[:5],
                    }
                )
                if offer_mode:
                    entry["amount"] = _round2(median)
                    entry["range_amount"] = [
                        _round2(min(stats.values)),
                        _round2(max(stats.values)),
                    ]
                else:
                    entry["share_pct"] = round(median * 100, 2)
                    entry["amount"] = _round2(median * float(base.nmck or 0))
                    entry["range_pct"] = [
                        round(min(stats.values) * 100, 2),
                        round(max(stats.values) * 100, 2),
                    ]
                if canon in OVERHEAD_KEYS:
                    covered_overheads.add(canon)
        lines.append(entry)

    _apply_sbcp_fallback(
        lines,
        nmck=base.nmck,
        object_kind=object_kind,
        design_stage=design_stage,
        warnings=warnings,
    )

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
                "amount": _round2(pct / 100 * base.nmck) if base.nmck is not None else None,
                "source": "ai",
                "n_analogs": 0,
                "range_pct": [round(bounds[0], 2), round(bounds[1], 2)],
                "analog_titles": [],
            }
        )

    payload: dict[str, Any] = {
        "base": {
            "nmck": _round2(base.nmck) if base.nmck is not None else None,
            "mode": "offer" if offer_mode else base.mode,
            "pd_share_pct": base.pd_share_pct,
            "rationale": base.rationale,
            "quote": base.quote,
            "object_kind": object_kind,
            "design_stage": design_stage,
        },
        "params": {
            "min_margin_pct": p.min_margin_pct,
            "target_margin_pct": p.target_margin_pct,
            "reductions": list(p.reductions),
            "offer_margins": list(p.offer_margins),
        },
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
    target_margin_pct: float | None = None,
) -> dict[str, Any]:
    """Правки человека: {'l0': {'amount': …} | {'share_pct': …}, 'o1': …} -> новый payload.

    Ключ — префикс l (lines) / o (overheads) + индекс. Задан amount — доля выводится из
    него; задана share_pct — сумма из доли (в режиме предложения — от текущей цены).
    Правленая строка помечается source='user'.
    """
    result: dict[str, Any] = dict(payload)
    result["lines"] = [dict(line) for line in payload.get("lines", [])]
    result["overheads"] = [dict(line) for line in payload.get("overheads", [])]
    result["params"] = dict(payload.get("params", {}))
    base_data = result.get("base", {})
    offer_mode = base_data.get("mode") == "offer" or base_data.get("nmck") is None
    # опорная цена для пересчёта долей <-> сумм: НМЦК либо текущая цена предложения
    if base_data.get("nmck") is not None:
        ref_price = float(base_data["nmck"])
    else:
        totals = payload.get("totals", {})
        ref_price = float(totals.get("price") or 0.0)

    for key, edit in line_edits.items():
        bucket = result["lines"] if key.startswith("l") else result["overheads"]
        try:
            idx = int(key[1:])
            line = bucket[idx]
        except (ValueError, IndexError):
            continue
        amount = edit.get("amount")
        share_pct = edit.get("share_pct")
        is_overhead = key.startswith("o")
        if amount is not None and not (offer_mode and is_overhead):
            line["amount"] = _round2(amount)
            line["share_pct"] = round(amount / ref_price * 100, 2) if ref_price else None
        elif share_pct is not None or (amount is not None and offer_mode and is_overhead):
            # накладные в режиме предложения задаются процентом (сумма зависит от цены)
            if share_pct is None and amount is not None and ref_price:
                share_pct = amount / ref_price * 100
            if share_pct is None:
                continue
            line["share_pct"] = round(share_pct, 2)
            if not (offer_mode and is_overhead):
                line["amount"] = _round2(share_pct / 100 * ref_price) if ref_price else None
        else:
            continue
        line["source"] = "user"

    if min_margin_pct is not None:
        result["params"]["min_margin_pct"] = min_margin_pct
    if target_margin_pct is not None:
        result["params"]["target_margin_pct"] = target_margin_pct
    _recompute_totals(result)
    return result


def _recompute_totals(payload: dict[str, Any]) -> None:
    """Итоги, сетки и предупреждения — из строк payload (in-place). Оба режима."""
    base = payload["base"]
    warnings: list[str] = []
    no_data = [line["name"] for line in payload["lines"] if line.get("amount") is None]

    if base.get("mode") == "offer" or base.get("nmck") is None:
        _recompute_offer(payload, warnings)
    else:
        _recompute_with_nmck(payload, warnings)

    if no_data:
        warnings.append(
            "Разделы без данных по аналогам (в сумме НЕ учтены, нужна ручная оценка): "
            + "; ".join(no_data)
        )
    payload["no_data"] = no_data
    payload["warnings"] = warnings + list(payload.get("warnings_static", []))


def _recompute_with_nmck(payload: dict[str, Any], warnings: list[str]) -> None:
    """Классический режим: доли от НМЦК, сетка понижения, минимальная цена."""
    nmck = float(payload["base"]["nmck"])
    params = payload["params"]
    all_lines = list(payload["lines"]) + list(payload["overheads"])
    cost = sum(float(line["amount"]) for line in all_lines if line.get("amount") is not None)

    profit = nmck - cost
    payload["totals"] = {
        "mode": "nmck",
        "cost": _round2(cost),
        "price": _round2(nmck),
        "profit_at_nmck": _round2(profit),
        "margin_pct": round(profit / nmck * 100, 1) if nmck else None,
    }

    reductions = [float(r) for r in params.get("reductions", DEFAULT_REDUCTIONS)]
    payload["scenarios"] = [
        {
            "reduction_pct": r,
            "price": _round2(nmck * (1 - r / 100)),
            "profit": _round2(nmck * (1 - r / 100) - cost),
            "margin_pct": round((nmck * (1 - r / 100) - cost) / nmck * 100, 1) if nmck else None,
        }
        for r in reductions
    ]

    min_margin = float(params.get("min_margin_pct", DEFAULT_MIN_MARGIN_PCT))
    min_price = cost * (1 + min_margin / 100)
    max_reduction = (nmck - min_price) / nmck * 100 if nmck else 0.0
    payload["min_price"] = {
        "price": _round2(min_price),
        "min_margin_pct": min_margin,
        "max_reduction_pct": round(max_reduction, 1),
    }

    if nmck and cost > nmck:
        warnings.insert(
            0,
            "⚠ Себестоимость ВЫШЕ НМЦК: участие убыточно при текущей цене "
            f"(себестоимость {cost:,.0f} ₽ против НМЦК {nmck:,.0f} ₽).".replace(",", " "),
        )
    elif nmck and min_price > nmck:
        warnings.insert(
            0,
            "⚠ Даже без снижения цены маржа ниже целевой "
            f"({payload['totals']['margin_pct']}% при целевых {min_margin:.0f}%).",
        )


def _offer_price(cost_sections: float, overhead_pct: float, margin_pct: float) -> float:
    """Цена предложения: себестоимость разделов / (1 − накладные% − маржа%)."""
    denominator = max(1 - (overhead_pct + margin_pct) / 100, _MIN_OFFER_DENOMINATOR)
    return cost_sections / denominator


def _recompute_offer(payload: dict[str, Any], warnings: list[str]) -> None:
    """Режим предложения (НМЦК нет): цена формируется от себестоимости и маржи."""
    params = payload["params"]
    target_margin = float(params.get("target_margin_pct", DEFAULT_TARGET_MARGIN_PCT))
    min_margin = float(params.get("min_margin_pct", DEFAULT_MIN_MARGIN_PCT))

    cost_sections = sum(
        float(line["amount"]) for line in payload["lines"] if line.get("amount") is not None
    )
    overhead_pct = sum(
        float(line["share_pct"])
        for line in payload["overheads"]
        if line.get("share_pct") is not None
    )
    denominator_floor_hit = 1 - (overhead_pct + target_margin) / 100 < _MIN_OFFER_DENOMINATOR
    price = _offer_price(cost_sections, overhead_pct, target_margin)

    for line in payload["overheads"]:
        pct = line.get("share_pct")
        line["amount"] = _round2(float(pct) / 100 * price) if pct is not None else None
    for line in payload["lines"]:
        amount = line.get("amount")
        line["share_pct"] = round(float(amount) / price * 100, 2) if amount and price else None

    cost_total = cost_sections + sum(
        float(line["amount"]) for line in payload["overheads"] if line.get("amount") is not None
    )
    payload["totals"] = {
        "mode": "offer",
        "cost": _round2(cost_total),
        "price": _round2(price),
        "profit_at_offer": _round2(price - cost_total),
        "margin_pct": round(target_margin, 1),
    }

    margins = [float(m) for m in params.get("offer_margins", DEFAULT_OFFER_MARGINS)]
    payload["scenarios"] = [
        {
            "margin_pct": m,
            "price": _round2(_offer_price(cost_sections, overhead_pct, m)),
            "profit": _round2(_offer_price(cost_sections, overhead_pct, m) * m / 100),
        }
        for m in margins
    ]
    payload["min_price"] = {
        "price": _round2(_offer_price(cost_sections, overhead_pct, min_margin)),
        "min_margin_pct": min_margin,
        "max_reduction_pct": None,
    }

    warnings.insert(
        0,
        "НМЦК в тендере не указана — цена сформирована ОТ СЕБЕСТОИМОСТИ: суммы разделов "
        "— медианы затрат по проектам-аналогам (₽), накладные — % от цены, маржа "
        f"{target_margin:.0f}% (медиана прибыли бюро; правится ниже).",
    )
    if denominator_floor_hit:
        warnings.insert(
            0,
            "⚠ Накладные + маржа съедают почти всю цену — формула ограничена, "
            "проверьте проценты накладных и целевую маржу.",
        )
