"""Гос/негос экспертиза ПД по ст. 49 ГрК РФ — детерминированные правила, не LLM.

Логика (источник — ст. 49 ГрК РФ, сверено с юристами заказчика 2026-07):
1) Экспертиза НЕ проводится: капремонт (ч. 3), малые объекты ч. 2 (нежилые/производственные
   до 2 этажей и 1500 м² — кроме опасных/уникальных; жилые до 3 этажей — без бюджета).
2) Гос экспертиза ОБЯЗАТЕЛЬНА: бюджетное финансирование (все 44-ФЗ и 615 ПП — бюджет),
   ОКН, особые территории (ООПТ/шельф/морские воды), проверка сметной стоимости.
3) Иначе — по выбору застройщика допустима НЕГОС: заключение юридически равнозначно,
   существенно дешевле и быстрее; с учётом затрат на устранение замечаний негос
   обходится в ~3–5% от стоимости варианта с гос экспертизой (оценка бюро).

Вход — drivers из брифа ТЗ (могут быть неполными: старые брифы без drivers) + закон
закупки из карточки. Неизвестно -> честный verdict 'unknown' с тем, что удалось понять.
"""

from __future__ import annotations

from typing import Any

_BUDGET_LAWS = ("44-ФЗ", "615 ПП")

NONGOV_SAVINGS_NOTE = (
    "Негосударственная экспертиза допустима (ст. 49 ГрК): заключение равнозначно "
    "государственному, проходит быстрее, а с учётом затрат на устранение замечаний "
    "обходится в ~3–5% от стоимости варианта с гос экспертизой."
)


def _small_object_exempt(drivers: dict[str, Any], budget: bool | None) -> str | None:
    """Ч. 2 ст. 49: малые объекты, экспертиза не требуется. -> причина или None."""
    if drivers.get("hazardous_or_unique"):
        return None
    floors = drivers.get("floors")
    area = drivers.get("area_m2")
    use = drivers.get("object_use")
    is_small_nonres = (
        use in ("nonresidential", "industrial")
        and floors is not None
        and floors <= 2
        and area is not None
        and area <= 1500
    )
    if is_small_nonres:
        return (
            "отдельно стоящий нежилой объект до 2 этажей и до 1500 м² "
            "(ч. 2 ст. 49 ГрК) — экспертиза не требуется"
        )
    if use == "residential" and floors is not None and floors <= 3 and budget is False:
        return (
            "жилой объект до 3 этажей без бюджетного финансирования "
            "(ч. 2 ст. 49 ГрК) — экспертиза может не требоваться"
        )
    return None


def assess_expertise(brief: dict[str, Any], law: str | None) -> dict[str, Any]:
    """Оценка режима экспертизы: verdict + причины + рекомендация для расчёта.

    verdict: state_required | nongov_allowed | not_required | unknown.
    """
    drivers_raw = brief.get("drivers")
    drivers: dict[str, Any] = drivers_raw if isinstance(drivers_raw, dict) else {}
    reasons: list[str] = []

    budget: bool | None = drivers.get("budget_funded")
    if budget is None and law:
        if any(marker in law for marker in _BUDGET_LAWS):
            budget = True
            reasons.append(f"закупка по {law} — финансирование бюджетное")
        elif law == "Коммерческие":
            budget = False

    # 1) капремонт: экспертиза разделов ПД не проводится (ч. 3 ст. 49)
    if drivers.get("kapremont"):
        return {
            "verdict": "not_required",
            "reasons": [
                *reasons,
                "предмет — капитальный ремонт: экспертиза разделов ПД не проводится "
                "(ч. 3 ст. 49 ГрК)",
            ],
            "recommendation": (
                "Экспертиза ПД для капремонта не требуется — если в расчёте есть строка "
                "экспертизы, проверьте её необходимость (заказчик может требовать "
                "только НМЦК-проверку смет)."
            ),
        }

    # 2) малые объекты ч. 2 ст. 49
    small = _small_object_exempt(drivers, budget)
    if small is not None:
        return {
            "verdict": "not_required",
            "reasons": [*reasons, small],
            "recommendation": (
                "Экспертиза не обязательна для этого объекта — закладывать её в "
                "себестоимость стоит только если её прямо требует ТЗ."
            ),
        }

    # 3) обязательная гос
    if drivers.get("okn"):
        reasons.append("объект культурного наследия — гос экспертиза в силу закона")
    if drivers.get("special_territory"):
        reasons.append("особые территории (ООПТ/шельф/морские воды) — только гос экспертиза")
    if budget and not any("бюджет" in r for r in reasons):
        reasons.append("бюджетное финансирование — гос экспертиза обязательна")
    if drivers.get("okn") or drivers.get("special_territory") or budget:
        return {
            "verdict": "state_required",
            "reasons": reasons,
            "recommendation": (
                "Гос экспертиза обязательна: закладывайте её сроки (включая повторные "
                "подачи) и затраты на устранение замечаний в резерв."
            ),
        }

    # 4) явное указание в ТЗ — уважаем, если правила выше не сработали
    in_tz = drivers.get("expertise_in_tz")
    if in_tz == "state":
        return {
            "verdict": "state_required",
            "reasons": [*reasons, "ТЗ прямо требует государственную экспертизу"],
            "recommendation": "Гос экспертиза по требованию ТЗ — заложите сроки и резерв.",
        }
    if in_tz == "none":
        return {
            "verdict": "not_required",
            "reasons": [*reasons, "по ТЗ экспертиза не требуется"],
            "recommendation": "Экспертиза по ТЗ не требуется — не закладывайте её в расчёт.",
        }

    # 5) негос допустима
    if budget is False or in_tz == "nongov":
        return {
            "verdict": "nongov_allowed",
            "reasons": [
                *reasons,
                "обязательных оснований для гос экспертизы не видно — допустима негос",
            ],
            "recommendation": NONGOV_SAVINGS_NOTE,
        }

    return {
        "verdict": "unknown",
        "reasons": [
            *reasons,
            "недостаточно данных (нет драйверов из ТЗ и однозначного закона закупки)",
        ],
        "recommendation": (
            "Режим экспертизы не определён — уточните: бюджетное ли финансирование, "
            "капремонт ли это, параметры объекта (этажность, площадь, ОКН)."
        ),
    }
