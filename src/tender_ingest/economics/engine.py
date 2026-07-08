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

import math
import statistics
from dataclasses import dataclass, field
from typing import Any

from tender_ingest.economics.canon import (
    AGGREGATE_DESIGN_KEYS,
    CATALOG_BY_KEY,
    COMPOSITE_SECTIONS,
    detect_aggregate,
)
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
    weights: list[float] = field(default_factory=list)  # похожесть аналога (масштаб, лист)


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


def _analog_weight(project: AnalogProject, target_total: float | None) -> float:
    """Похожесть аналога: факт-лист достовернее прикидки, близкий масштаб — важнее.

    Масштабный вес 1/(1+|ln(цена_аналога/целевая_цена)|): равный масштаб -> 1,
    контракт в e раз больше/меньше -> 0.5, на порядок -> ~0.3.
    """
    weight = 1.0 if project.sheet == "work" else 0.6
    if target_total and project.contract_total:
        weight *= 1.0 / (1.0 + abs(math.log(float(project.contract_total) / target_total)))
    return weight


def _weighted_median(values: list[float], weights: list[float]) -> float:
    """Взвешенная медиана: значение, на котором накапливается половина суммы весов.

    Если половина весов набирается ровно на границе (как у обычной медианы чётного
    набора с равными весами) — среднее двух соседних значений.
    """
    pairs = sorted(zip(values, weights, strict=True))
    total = sum(w for _, w in pairs)
    if total <= 0:
        return statistics.median(values)
    acc = 0.0
    for i, (value, weight) in enumerate(pairs):
        acc += weight
        if math.isclose(acc, total / 2, rel_tol=1e-9):
            nxt = pairs[i + 1][0] if i + 1 < len(pairs) else value
            return (value + nxt) / 2
        if acc > total / 2:
            return value
    return pairs[-1][0]


def _section_stats(
    canon: str,
    analogs: list[AnalogProject],
    *,
    absolute: bool,
    target_total: float | None = None,
) -> _Stats:
    """Статистика раздела по аналогам: доли от цены (absolute=False) или суммы в ₽."""
    stats = _Stats()
    for project in analogs:
        if absolute:
            value = project.amounts.get(canon)
            if value is not None and value > 0:
                stats.values.append(value)
                stats.titles.append(project.title)
                stats.weights.append(_analog_weight(project, target_total))
        else:
            share = project.sections.get(canon)
            if share is not None and 0 < share < 0.9:
                stats.values.append(share)
                stats.titles.append(project.title)
                stats.weights.append(_analog_weight(project, target_total))
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


def _occupied_design_canons(lines: list[dict[str, Any]]) -> set[str]:
    """Design-каноны, занятые обычными строками расчёта (для анти-задвоения агрегатов).

    Композиты раскрываются в обе стороны: занят ar -> исключаем и ar, и ar_kr аналога;
    занят ar_kr -> исключаем ar_kr, ar и kr. Лучше слегка занизить агрегат, чем задвоить.
    """
    occupied: set[str] = set()
    for line in lines:
        canon = line.get("canon")
        if not canon or canon in AGGREGATE_DESIGN_KEYS:
            continue
        if line.get("group") != "design":
            continue
        occupied.add(canon)
        for composite, parts in COMPOSITE_SECTIONS.items():
            if canon == composite:
                occupied.update(parts)
            elif canon in parts:
                occupied.add(composite)
    return occupied


def _aggregate_stats(
    factor: float,
    analogs: list[AnalogProject],
    excluded: set[str],
    *,
    absolute: bool,
    target_total: float | None,
) -> _Stats:
    """Производная статистика агрегата ПД/РД: доля от суммы design-группы аналога.

    По каждому аналогу: D = сумма его design-разделов (суммы в offer-режиме, доли в
    nmck-режиме) без занятых канонов и без самих агрегатов; значение = factor × D.
    Аналог участвует, если у него ≥3 design-канонов с данными (разреженный занижает).
    """
    stats = _Stats()
    for project in analogs:
        data = project.amounts if absolute else project.sections
        total = 0.0
        matched = 0
        for canon, value in data.items():
            if canon in excluded or canon in AGGREGATE_DESIGN_KEYS:
                continue
            section = CATALOG_BY_KEY.get(canon)
            if section is None or section.group != "design":
                continue
            if value is None or value <= 0:
                continue
            if not absolute and value >= 0.9:
                continue
            total += float(value)
            matched += 1
        if matched < 3 or total <= 0:
            continue
        if not absolute and total >= 0.95:
            continue
        stats.values.append(factor * total)
        stats.titles.append(project.title)
        stats.weights.append(_analog_weight(project, target_total))
    return stats


def _apply_aggregate_estimates(
    lines: list[dict[str, Any]],
    analogs: list[AnalogProject],
    *,
    nmck: float | None,
    warnings: list[str],
) -> None:
    """Оценить агрегаты «ПД/РД целиком» производно от design-группы аналогов.

    Прямой статистики по агрегатам в базе нет (строки бюро пораздельные), поэтому
    сумма design-группы аналога делится по п. 1.5 СБЦП (ПД 40% / РД 60%). Разделы,
    уже расписанные отдельными строками, исключаются — иначе двойной счёт.
    """
    absolute = nmck is None
    occupied = _occupied_design_canons(lines)
    seen_aggregates: set[str] = set()
    for line in lines:
        canon = line.get("canon")
        if canon not in AGGREGATE_DESIGN_KEYS or line.get("source") != "no_data":
            continue
        if canon in seen_aggregates:
            warnings.append(
                f"Агрегат «{line['name']}» повторяется — вторая строка оставлена без "
                "оценки, проверьте состав расчёта."
            )
            continue
        seen_aggregates.add(canon)
        factor = AGGREGATE_DESIGN_KEYS[str(canon)]
        stats = _aggregate_stats(
            factor, analogs, occupied, absolute=absolute, target_total=nmck
        )
        if not stats.values:
            continue  # остаётся no_data — честнее, чем выдумывать
        median = _weighted_median(stats.values, stats.weights)
        note = (
            "производная оценка: сумма разделов ПД/РД аналогов минус разделы, уже "
            f"расписанные отдельными строками; доля стадии {factor:.0%} "
            "(п. 1.5 СБЦП: ПД 40 / РД 60). Точнее — расписать состав по разделам."
        )
        line.update(
            {
                "source": "derived",
                "n_analogs": len(stats.values),
                "analog_titles": stats.titles[:5],
                "note": note,
            }
        )
        if absolute:
            line["amount"] = _round2(median)
            line["range_amount"] = [_round2(min(stats.values)), _round2(max(stats.values))]
        else:
            line["share_pct"] = round(median * 100, 2)
            line["amount"] = _round2(median * float(nmck or 0))
            line["range_pct"] = [
                round(min(stats.values) * 100, 2),
                round(max(stats.values) * 100, 2),
            ]
    if seen_aggregates and "pd_rd_total" in seen_aggregates and len(seen_aggregates) > 1:
        warnings.append(
            "В расчёте одновременно «ПД+РД целиком» и отдельные агрегаты ПД/РД — "
            "возможен двойной счёт, проверьте строки."
        )


def _plan_fact_coef(
    canon: str | None, plan_fact: dict[str, tuple[float, int]] | None
) -> tuple[float, int] | None:
    """Коэффициент факт/план для раздела: канон -> группа -> общий по базе."""
    if not plan_fact:
        return None
    if canon and canon in plan_fact:
        return plan_fact[canon]
    if canon and canon in CATALOG_BY_KEY:
        group_key = f"__group_{CATALOG_BY_KEY[canon].group}"
        if group_key in plan_fact:
            return plan_fact[group_key]
    return plan_fact.get("__all__")


def _apply_plan_fact(
    lines: list[dict[str, Any]],
    plan_fact: dict[str, tuple[float, int]] | None,
    *,
    nmck: float | None,
    warnings: list[str],
) -> None:
    """Поправка на историю план/факт бюро: реальные затраты против плановых.

    Применяется к строкам с расчётным значением (аналоги/СБЦП). Порог 5% —
    меньшее отклонение считаем шумом.
    """
    corrected = 0
    for line in lines:
        if line.get("amount") is None or line.get("source") not in ("analog", "sbcp", "derived"):
            continue
        found = _plan_fact_coef(line.get("canon"), plan_fact)
        if found is None:
            continue
        coef, n = found
        if abs(coef - 1.0) < 0.05:
            continue
        line["amount"] = _round2(float(line["amount"]) * coef)
        if nmck:
            line["share_pct"] = round(float(line["amount"]) / nmck * 100, 2)
        extra = f" · {line['note']}" if line.get("note") else ""
        line["note"] = f"план/факт бюро ×{coef:.2f} (по {n} строкам истории){extra}"
        corrected += 1
    if corrected:
        warnings.append(
            f"Поправка на историю план/факт: {corrected} разд. скорректированы — "
            "по завершённым проектам бюро фактические затраты отклоняются от плановых."
        )


def _totals_range(
    payload: dict[str, Any], analogs: list[AnalogProject], *, offer_mode: bool
) -> dict[str, Any] | None:
    """Диапазон себестоимости: итог пересчитывается по каждому аналогу отдельно.

    Аналог участвует, если покрывает хотя бы половину строк с данными; непокрытые
    строки берутся по текущему (медианному) значению. Накладные — константой.
    """
    nmck = payload["base"].get("nmck")
    overheads_total = sum(
        float(line["amount"]) for line in payload["overheads"] if line.get("amount") is not None
    )
    totals: list[float] = []
    for analog in analogs:
        total = 0.0
        counted = 0
        matched = 0
        for line in payload["lines"]:
            amount = line.get("amount")
            if amount is None:
                continue
            counted += 1
            canon = line.get("canon")
            if canon and offer_mode and canon in analog.amounts:
                total += analog.amounts[canon]
                matched += 1
            elif canon and not offer_mode and nmck and canon in analog.sections:
                total += analog.sections[canon] * float(nmck)
                matched += 1
            else:
                total += float(amount)
        if counted and matched >= max(1, counted // 2):
            totals.append(total + overheads_total)
    if len(totals) < 3:
        return None
    return {
        "p25": _round2(_percentile(totals, 0.25)),
        "p75": _round2(_percentile(totals, 0.75)),
        "n": len(totals),
    }


def _sbcp_check(
    lines: list[dict[str, Any]],
    *,
    nmck: float | None,
    object_kind: str,
    design_stage: str,
    warnings: list[str],
) -> dict[str, Any] | None:
    """Нормативный контур СБЦП: параллельная оценка КАЖДОГО раздела с весом норматива.

    Тот же принцип приведения к уровню бюро, что и в фолбэке (k по разделам-якорям),
    но считается для всех разделов — это независимая сверка «история бюро vs норматив».
    Заполняет line['sbcp_amount'] и возвращает сводку с относительным расхождением.
    """
    if object_kind != "building":
        return None
    weights = sbcp_weights(design_stage)

    def anchor_value(line: dict[str, Any]) -> float | None:
        if nmck is not None:
            share = line.get("share_pct")
            return float(share) / 100.0 if share is not None else None
        amount = line.get("amount")
        return float(amount) if amount is not None else None

    anchors = [
        (value, weights[canon])
        for line in lines
        if line["source"] in ("analog", "user")
        and (canon := line.get("canon")) in weights
        and (value := anchor_value(line)) is not None
    ]
    if len(anchors) < 2:
        return None
    k = statistics.median(value / weight for value, weight in anchors)

    total_bureau = 0.0
    total_sbcp = 0.0
    worst: list[tuple[str, float]] = []  # (название, отклонение в %)
    for line in lines:
        canon = line.get("canon")
        if canon not in weights:
            continue
        scaled = weights[str(canon)] * k
        sbcp_amount = scaled * float(nmck) if nmck is not None else scaled
        line["sbcp_amount"] = _round2(sbcp_amount)
        amount = line.get("amount")
        if amount is None or sbcp_amount <= 0:
            continue
        total_bureau += float(amount)
        total_sbcp += sbcp_amount
        deviation = (float(amount) - sbcp_amount) / sbcp_amount * 100
        if abs(deviation) > 30:
            worst.append((str(line["name"]), round(deviation, 0)))

    if total_sbcp <= 0:
        return None
    total_dev = (total_bureau - total_sbcp) / total_sbcp * 100
    check = {
        "total_bureau": _round2(total_bureau),
        "total_sbcp": _round2(total_sbcp),
        "deviation_pct": round(total_dev, 1),
        "anchors": len(anchors),
        "source": SBCP_SOURCE,
        "stage": stage_label(design_stage),
    }
    if abs(total_dev) > 30:
        warnings.append(
            f"⚠ Сверка с нормативами СБЦП: итог по разделам отклоняется на "
            f"{total_dev:+.0f}% от нормативной структуры — проверьте выбор аналогов."
        )
    elif worst:
        names = "; ".join(f"{name} ({dev:+.0f}%)" for name, dev in worst[:3])
        warnings.append(f"Сверка с СБЦП: сильные отклонения по разделам — {names}.")
    return check


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
    plan_fact: dict[str, tuple[float, int]] | None = None,
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
        if canon is None:
            # фолбэк: ИИ не подобрал канон, но имя — агрегат стадии («ПД», «РД»…)
            canon = detect_aggregate(section.name)
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
        # агрегаты минуют пораздельную статистику (в базе их нет) — их оценивает
        # _apply_aggregate_estimates производно от design-группы аналогов
        if canon is not None and canon not in AGGREGATE_DESIGN_KEYS:
            stats = _section_stats(
                canon, analogs, absolute=offer_mode, target_total=base.nmck
            )
            if stats.values:
                median = _weighted_median(stats.values, stats.weights)
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

    _apply_aggregate_estimates(lines, analogs, nmck=base.nmck, warnings=warnings)
    _apply_sbcp_fallback(
        lines,
        nmck=base.nmck,
        object_kind=object_kind,
        design_stage=design_stage,
        warnings=warnings,
    )
    _apply_plan_fact(lines, plan_fact, nmck=base.nmck, warnings=warnings)

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
    payload["totals_range"] = _totals_range(payload, analogs, offer_mode=offer_mode)
    payload["sbcp_check"] = _sbcp_check(
        lines,
        nmck=base.nmck,
        object_kind=object_kind,
        design_stage=design_stage,
        warnings=warnings,
    )
    # _sbcp_check дописывает warnings_static после _recompute_totals — синхронизируем
    payload["warnings"] = list(payload.get("warnings", []))
    for warning in warnings:
        if warning not in payload["warnings"]:
            payload["warnings"].append(warning)
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


def canon_median_hints(
    analogs: list[AnalogProject], *, nmck: float | None
) -> dict[str, dict[str, Any]]:
    """Подсказки редактору по каждому канону с данными у аналогов расчёта.

    hints — метаданные строки (диапазон, число аналогов), prefill — стартовые
    значения для новой строки (медиана долей × НМЦК либо медиана сумм в offer-режиме).
    """
    offer_mode = nmck is None
    out: dict[str, dict[str, Any]] = {}
    for canon in CATALOG_BY_KEY:
        stats = _section_stats(canon, analogs, absolute=offer_mode)
        if not stats.values:
            continue
        median = statistics.median(stats.values)
        hints: dict[str, Any] = {"n_analogs": len(stats.values), "analog_titles": stats.titles[:5]}
        prefill: dict[str, Any]
        if offer_mode:
            hints["range_amount"] = [_round2(min(stats.values)), _round2(max(stats.values))]
            prefill = {"amount": _round2(median)}
        else:
            hints["range_pct"] = [
                round(min(stats.values) * 100, 2),
                round(max(stats.values) * 100, 2),
            ]
            prefill = {
                "share_pct": round(median * 100, 2),
                "amount": _round2(median * float(nmck or 0)),
            }
        out[canon] = {"hints": hints, "prefill": prefill}
    return out


def _editor_row(
    row_state: dict[str, Any],
    old_rows: list[dict[str, Any]],
    *,
    is_overhead: bool,
    offer_mode: bool,
    ref_price: float,
    canon_medians: dict[str, dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Одна строка из состояния редактора -> строка payload (None — пропустить).

    idx указывает на строку исходного payload (метаданные сохраняются), idx=None — новая.
    touched — какое поле правил человек последним: оно первично, второе выводится.
    """
    name = str(row_state.get("name") or "").strip()
    if not name:
        return None
    idx = row_state.get("idx")
    existing = (
        dict(old_rows[int(idx)]) if isinstance(idx, int) and 0 <= int(idx) < len(old_rows) else None
    )
    canon_raw = row_state.get("canon")
    canon = str(canon_raw) if canon_raw and str(canon_raw) in CATALOG_BY_KEY else None
    fallback_group = "overhead" if is_overhead else None
    group = CATALOG_BY_KEY[canon].group if canon else fallback_group

    if existing is not None:
        line = existing
        line["name"] = name
        if canon != line.get("canon"):
            line["canon"] = canon
            line["canon_label"] = CATALOG_BY_KEY[canon].label if canon else None
            line["group"] = group
            line["n_analogs"] = 0
            line["range_pct"] = None
            line.pop("range_amount", None)
            if canon and canon_medians and canon in canon_medians:
                line.update(canon_medians[canon].get("hints", {}))
    else:
        line = {
            "name": name,
            "canon": canon,
            "canon_label": CATALOG_BY_KEY[canon].label if canon else None,
            "group": group,
            "quote": "",
            "note": "добавлено вручную",
            "share_pct": None,
            "amount": None,
            "source": "user",
            "n_analogs": 0,
            "range_pct": None,
            "analog_titles": [],
        }
        if canon and canon_medians and canon in canon_medians:
            median = canon_medians[canon]
            line.update(median.get("hints", {}))
            # значение не введено — предзаполняем медианой аналогов
            if row_state.get("amount") is None and row_state.get("share_pct") is None:
                prefill = median.get("prefill", {})
                if prefill:
                    line.update(prefill)
                    line["source"] = "analog"
                    line["note"] = "медиана по аналогам (строка добавлена вручную)"

    touched = row_state.get("touched")
    amount = row_state.get("amount")
    share_pct = row_state.get("share_pct")
    # у новой строки «правкой» считается любое введённое значение
    if existing is None and touched is None and (amount is not None or share_pct is not None):
        touched = "amount" if amount is not None else "share_pct"

    if touched == "amount" and amount is not None:
        if offer_mode and is_overhead:
            # накладные в режиме предложения задаются процентом (сумма зависит от цены)
            if ref_price:
                line["share_pct"] = round(float(amount) / ref_price * 100, 2)
        else:
            line["amount"] = _round2(float(amount))
            line["share_pct"] = round(float(amount) / ref_price * 100, 2) if ref_price else None
        line["source"] = "user"
    elif touched == "share_pct" and share_pct is not None:
        line["share_pct"] = round(float(share_pct), 2)
        if not (offer_mode and is_overhead):
            line["amount"] = _round2(float(share_pct) / 100 * ref_price) if ref_price else None
        line["source"] = "user"
    return line


def apply_editor_state(
    payload: dict[str, Any],
    state: dict[str, Any],
    canon_medians: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Полное состояние редактора -> новый payload (append-only версия расчёта).

    state = {base: {nmck, object_kind, design_stage}, params: {min_margin_pct,
    target_margin_pct}, lines: [...], overheads: [...]}. Строки без idx — новые,
    отсутствующие в state строки payload — удалённые. canon_medians (по данным
    аналогов из БД) даёт новым строкам с каноном подсказки и предзаполнение.
    """
    result: dict[str, Any] = dict(payload)
    result["base"] = dict(payload.get("base", {}))
    result["params"] = dict(payload.get("params", {}))
    base = result["base"]

    base_state = state.get("base") or {}
    old_nmck = base.get("nmck")
    if "nmck" in base_state:
        new_nmck = base_state["nmck"]
        base["nmck"] = _round2(float(new_nmck)) if new_nmck is not None else None
        if base["nmck"] is None:
            base["mode"] = "offer"
        elif base.get("mode") == "offer":
            base["mode"] = "full"
    for key in ("object_kind", "design_stage"):
        if base_state.get(key):
            base[key] = str(base_state[key])

    params_state = state.get("params") or {}
    for key in ("min_margin_pct", "target_margin_pct"):
        if params_state.get(key) is not None:
            result["params"][key] = float(params_state[key])

    offer_mode = base.get("nmck") is None
    if base.get("nmck") is not None:
        ref_price = float(base["nmck"])
    else:
        ref_price = float(payload.get("totals", {}).get("price") or 0.0)

    old_lines = list(payload.get("lines", []))
    old_overheads = list(payload.get("overheads", []))
    result["lines"] = [
        row
        for row_state in state.get("lines", [])
        if (
            row := _editor_row(
                row_state,
                old_lines,
                is_overhead=False,
                offer_mode=offer_mode,
                ref_price=ref_price,
                canon_medians=canon_medians,
            )
        )
        is not None
    ]
    result["overheads"] = [
        row
        for row_state in state.get("overheads", [])
        if (
            row := _editor_row(
                row_state,
                old_overheads,
                is_overhead=True,
                offer_mode=offer_mode,
                ref_price=ref_price,
                canon_medians=canon_medians,
            )
        )
        is not None
    ]

    # НМЦК изменилась (режим тот же) — суммы разделов первичны, доли пересчитываем;
    # накладные первичны процентом, сумма выводится. В offer-режиме всё сделает
    # _recompute_offer.
    nmck_changed = base.get("nmck") is not None and (
        old_nmck is None or abs(float(base["nmck"]) - float(old_nmck)) > 0.004
    )
    if nmck_changed:
        nmck = float(base["nmck"])
        for line in result["lines"]:
            amount = line.get("amount")
            line["share_pct"] = round(float(amount) / nmck * 100, 2) if amount is not None else None
        for line in result["overheads"]:
            pct = line.get("share_pct")
            if pct is not None:
                line["amount"] = _round2(float(pct) / 100 * nmck)

    # диапазон по аналогам считался для исходных строк — после правок он неактуален
    result.pop("totals_range", None)
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
    # крупные проектные позиции без оценки — итог фактически занижен, кричим первым
    big_missing = [
        line["name"]
        for line in payload["lines"]
        if line.get("amount") is None
        and (line.get("group") == "design" or line.get("canon") in AGGREGATE_DESIGN_KEYS)
    ]
    if big_missing:
        warnings.insert(
            0,
            "⚠ Итог ЗАНИЖЕН: крупные проектные разделы без оценки — "
            + "; ".join(big_missing)
            + ". Себестоимость неполная, оцените их вручную.",
        )
    # один design-канон в нескольких строках — вероятен двойной счёт
    canon_counts: dict[str, int] = {}
    for line in payload["lines"]:
        canon = line.get("canon")
        if canon and canon not in AGGREGATE_DESIGN_KEYS and line.get("group") == "design":
            canon_counts[canon] = canon_counts.get(canon, 0) + 1
    doubled = sorted(c for c, n in canon_counts.items() if n > 1)
    if doubled:
        labels = ", ".join(CATALOG_BY_KEY[c].label for c in doubled if c in CATALOG_BY_KEY)
        warnings.append(f"Возможен двойной счёт: раздел(ы) в нескольких строках — {labels}.")
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
