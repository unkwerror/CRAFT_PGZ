"""Тесты экономики: canon-матчер, парсер «Экономики», движок расчёта."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from tender_ingest.economics.canon import CATALOG_BY_KEY, match_canon
from tender_ingest.economics.engine import (
    BaseInput,
    OverheadInput,
    Params,
    SectionInput,
    apply_editor_state,
    apply_edits,
    build_payload,
    canon_median_hints,
    overhead_history_ranges,
)
from tender_ingest.economics.sbcp import sbcp_weights
from tender_ingest.economics.store import AnalogProject
from tender_ingest.economics.xlsx import parse_workbook

FIXTURE = Path(__file__).parent / "fixtures" / "Экономика (2).xlsx"


# --- canon ---


def test_match_canon_frequent_names() -> None:
    cases = {
        "ГИП сопровождение ПСД": "gip",
        "Менеджер проекта": "project_manager",
        "Доп. затраты на проверку с/с работы фрилансера, выпуск документации": "freelance_check",
        "Резерв": "reserve",
        "ПЗУ(ГП)": "pzu",
        "Раздел 2. Схема планировочной организации земельного участка": "pzu",
        "АР(АР)": "ar",
        "КР(АС)": "kr",
        "ИОС2, ИОС3 (ВК, НВК)": "ios2_3",
        "СМЕТЫ": "smeta",
        "ГЭ СМ": "expertise_sm",
        "Экспертиза": "expertise_pd",
        "ОКНИ": "okni",
        "Инженерные изыскания (ИГДИ, ИГИ) - Горизонт Гео": "igdi",
        "Обеспечение сохранности ОКН": "mos",
        "Тех. Регламент обращения с отходами строит. И бытовыми": "waste_reg",
        "НО (Наружное освещение)": "ios1",
    }
    for name, expected in cases.items():
        assert match_canon(name) == expected, name


def test_match_canon_unknown_is_none() -> None:
    assert match_canon("Каргаполова Венера") is None
    assert match_canon("") is None


def test_overhead_keys_exist_in_catalog() -> None:
    for key in ("gip", "project_manager", "freelance_check", "reserve"):
        assert key in CATALOG_BY_KEY


# --- парсер ---


def test_parse_workbook_blocks_and_lines() -> None:
    projects = parse_workbook(FIXTURE)
    assert len(projects) == 96  # 31 «В РАБОТЕ» + 65 «ПРЕДВАРИТЕЛЬНЫЕ»
    assert sum(1 for p in projects if p.sheet == "work") == 31
    assert sum(1 for p in projects if p.sheet == "preliminary") == 65
    lines = [line for p in projects for line in p.lines]
    assert len(lines) > 2000
    # почти все строки распознаны в канон (в файле есть фамилии/фин-строки без канона)
    no_canon = sum(1 for line in lines if line.canon is None)
    assert no_canon / len(lines) < 0.05


def test_parse_workbook_first_block_values() -> None:
    project = parse_workbook(FIXTURE)[0]  # ЮТЕЙР
    assert project.contract_total == Decimal("830000")
    names = [line.canon for line in project.lines]
    assert "gip" in names or "gap" in names
    architecture = next(line for line in project.lines if line.name_raw == "Архитектура")
    assert architecture.canon == "ar"
    assert architecture.share is not None
    # доля = план/договор: 151850.4 / 830000
    assert abs(float(architecture.share) - 151850.4 / 830000) < 1e-6


def test_parse_number_never_int() -> None:
    projects = parse_workbook(FIXTURE)
    # «Итого ОСТАТОК…» не принимается за себестоимость (блок Лыжно-биатлонный центр)
    tarko = next(p for p in projects if "Лыжно-биатлонный" in p.title)
    assert tarko.cost_planned is None


# --- движок ---


def _analog(
    pid: int, sections: dict[str, float], amounts: dict[str, float] | None = None
) -> AnalogProject:
    return AnalogProject(
        id=pid,
        sheet="work",
        title=f"Проект {pid}",
        contract_total=1_000_000.0,
        sections=sections,
        section_names={},
        amounts=amounts or {},
    )


def _base_inputs() -> tuple[
    BaseInput, list[SectionInput], list[OverheadInput], list[AnalogProject]
]:
    base = BaseInput(nmck=10_000_000.0)
    sections = [
        SectionInput(name="Генеральный план", canon="pzu", quote="разработать ПЗУ"),
        SectionInput(name="Архитектурные решения", canon="ar"),
        SectionInput(name="Спецраздел без истории", canon=None),
    ]
    overheads = [
        OverheadInput(canon="gip", pct=5.0),
        OverheadInput(canon="reserve", pct=50.0),  # заведомо вне диапазона -> кламп
    ]
    analogs = [
        _analog(1, {"pzu": 0.02, "ar": 0.04, "gip": 0.05, "reserve": 0.05}),
        _analog(2, {"pzu": 0.03, "ar": 0.06, "gip": 0.04, "reserve": 0.06}),
        _analog(3, {"pzu": 0.04, "gip": 0.05, "reserve": 0.05}),
    ]
    return base, sections, overheads, analogs


def test_build_payload_medians_and_no_data() -> None:
    base, sections, overheads, analogs = _base_inputs()
    payload = build_payload(
        base=base,
        sections=sections,
        overheads=overheads,
        analogs=analogs,
        all_projects=analogs,
    )
    pzu = payload["lines"][0]
    assert pzu["source"] == "analog"
    assert pzu["share_pct"] == 3.0  # медиана 0.02/0.03/0.04
    assert pzu["amount"] == 300_000.0
    ar = payload["lines"][1]
    assert ar["n_analogs"] == 2  # только в двух аналогах
    assert ar["share_pct"] == 5.0
    unknown = payload["lines"][2]
    assert unknown["source"] == "no_data"
    assert unknown["amount"] is None
    assert any("без данных" in w.lower() for w in payload["warnings"])
    # накладная reserve клампится в исторический диапазон (fallback: до 17%)
    reserve = next(o for o in payload["overheads"] if o["canon"] == "reserve")
    assert reserve["share_pct"] < 50.0


def test_build_payload_totals_and_scenarios() -> None:
    base, sections, overheads, analogs = _base_inputs()
    payload = build_payload(
        base=base, sections=sections, overheads=overheads, analogs=analogs, all_projects=analogs
    )
    cost = payload["totals"]["cost"]
    lines_sum = sum(
        line["amount"]
        for line in payload["lines"] + payload["overheads"]
        if line["amount"] is not None
    )
    assert abs(cost - lines_sum) < 0.01
    assert payload["totals"]["profit_at_nmck"] == round(10_000_000.0 - cost, 2)
    sc10 = next(s for s in payload["scenarios"] if s["reduction_pct"] == 10.0)
    assert sc10["price"] == 9_000_000.0
    assert sc10["profit"] == round(9_000_000.0 - cost, 2)
    # мин. цена = себестоимость * (1 + маржа)
    assert payload["min_price"]["price"] == round(cost * 1.2, 2)


def test_build_payload_loss_warning() -> None:
    base = BaseInput(nmck=100_000.0)
    sections = [SectionInput(name="АР", canon="ar")]
    analogs = [_analog(1, {"ar": 0.95})]  # 0.95 отбрасывается фильтром < 0.9 -> возьмём 0.8
    analogs = [_analog(1, {"ar": 0.8}), _analog(2, {"ar": 0.85})]
    payload = build_payload(
        base=base,
        sections=sections,
        overheads=[OverheadInput(canon="reserve", pct=17.0)],
        analogs=analogs,
        all_projects=analogs,
    )
    # 82.5% + резерв 17% = 99.5% -> прибыль почти ноль; при клампе может быть и убыток —
    # главное: честное предупреждение о марже ниже целевой или убытке
    assert any(w.startswith("⚠") for w in payload["warnings"])


def test_apply_edits_recomputes() -> None:
    base, sections, overheads, analogs = _base_inputs()
    payload = build_payload(
        base=base, sections=sections, overheads=overheads, analogs=analogs, all_projects=analogs
    )
    old_cost = payload["totals"]["cost"]
    edited = apply_edits(payload, {"l2": {"amount": 500_000.0}}, min_margin_pct=30.0)
    line = edited["lines"][2]
    assert line["amount"] == 500_000.0
    assert line["source"] == "user"
    assert line["share_pct"] == 5.0
    assert edited["totals"]["cost"] == round(old_cost + 500_000.0, 2)
    assert edited["min_price"]["min_margin_pct"] == 30.0
    # исходный payload не изменён (правки строят новую версию)
    assert payload["lines"][2]["amount"] is None
    # повторный пересчёт не дублирует предупреждения
    again = apply_edits(edited, {})
    assert len(again["warnings"]) == len(edited["warnings"])


def _editor_rows(payload: dict, bucket: str) -> list[dict]:
    """Состояние редактора «как есть» из payload (без правок)."""
    return [
        {
            "idx": i,
            "name": line["name"],
            "canon": line.get("canon"),
            "amount": line.get("amount"),
            "share_pct": line.get("share_pct"),
            "touched": None,
        }
        for i, line in enumerate(payload[bucket])
    ]


def test_apply_editor_state_edit_delete_rename() -> None:
    base, sections, overheads, analogs = _base_inputs()
    payload = build_payload(
        base=base, sections=sections, overheads=overheads, analogs=analogs, all_projects=analogs
    )
    lines = _editor_rows(payload, "lines")
    lines[0]["name"] = "ПЗУ (переименовано)"
    lines[1]["amount"] = 700_000.0
    lines[1]["touched"] = "amount"
    del lines[2]  # удаляем строку без данных
    state = {"lines": lines, "overheads": _editor_rows(payload, "overheads")}
    edited = apply_editor_state(payload, state)

    assert len(edited["lines"]) == 2
    assert edited["lines"][0]["name"] == "ПЗУ (переименовано)"
    assert edited["lines"][0]["source"] == "analog"  # переименование не трогает значения
    assert edited["lines"][1]["amount"] == 700_000.0
    assert edited["lines"][1]["share_pct"] == 7.0  # от НМЦК 10 млн
    assert edited["lines"][1]["source"] == "user"
    assert edited["totals"]["cost"] == round(
        sum(
            line["amount"]
            for line in edited["lines"] + edited["overheads"]
            if line["amount"] is not None
        ),
        2,
    )


def test_apply_editor_state_add_row_with_canon_prefill() -> None:
    base, sections, overheads, analogs = _base_inputs()
    payload = build_payload(
        base=base, sections=sections, overheads=overheads, analogs=analogs, all_projects=analogs
    )
    medians = canon_median_hints(analogs, nmck=10_000_000.0)
    lines = _editor_rows(payload, "lines")
    lines.append({"idx": None, "name": "Генплан доп.", "canon": "pzu", "amount": None,
                  "share_pct": None, "touched": None})
    lines.append({"idx": None, "name": "Своя строка", "canon": None, "amount": 100_000.0,
                  "share_pct": None, "touched": None})
    state = {"lines": lines, "overheads": _editor_rows(payload, "overheads")}
    edited = apply_editor_state(payload, state, canon_medians=medians)

    prefilled = edited["lines"][-2]
    assert prefilled["source"] == "analog"  # медиана pzu = 3% от 10 млн
    assert prefilled["share_pct"] == 3.0
    assert prefilled["amount"] == 300_000.0
    manual = edited["lines"][-1]
    assert manual["source"] == "user"
    assert manual["amount"] == 100_000.0
    assert manual["share_pct"] == 1.0


def test_apply_editor_state_nmck_change_and_offer_switch() -> None:
    base, sections, overheads, analogs = _base_inputs()
    payload = build_payload(
        base=base, sections=sections, overheads=overheads, analogs=analogs, all_projects=analogs
    )
    state = {
        "lines": _editor_rows(payload, "lines"),
        "overheads": _editor_rows(payload, "overheads"),
        "base": {"nmck": 5_000_000.0},
    }
    edited = apply_editor_state(payload, state)
    # суммы разделов первичны: доля пересчитана от новой НМЦК
    line0 = edited["lines"][0]
    assert line0["share_pct"] == round(line0["amount"] / 5_000_000.0 * 100, 2)
    # накладные первичны процентом: сумма пересчитана
    oh0 = edited["overheads"][0]
    assert oh0["amount"] == round(oh0["share_pct"] / 100 * 5_000_000.0, 2)
    assert edited["totals"]["price"] == 5_000_000.0

    # очистили НМЦК -> режим предложения от себестоимости
    state2 = {
        "lines": _editor_rows(edited, "lines"),
        "overheads": _editor_rows(edited, "overheads"),
        "base": {"nmck": None},
    }
    offered = apply_editor_state(edited, state2)
    assert offered["base"]["mode"] == "offer"
    assert offered["totals"]["mode"] == "offer"
    assert offered["totals"]["price"] > 0


def test_weighted_analogs_prefer_close_scale() -> None:
    """Аналог близкого масштаба перевешивает: доля берётся ближе к его значению."""
    base = BaseInput(nmck=10_000_000.0)
    sections = [SectionInput(name="АР", canon="ar")]
    analogs = [
        AnalogProject(id=1, sheet="work", title="Близкий", contract_total=9_000_000.0,
                      sections={"ar": 0.05}, section_names={}, amounts={}),
        AnalogProject(id=2, sheet="work", title="Крупный", contract_total=200_000_000.0,
                      sections={"ar": 0.11}, section_names={}, amounts={}),
        AnalogProject(id=3, sheet="preliminary", title="Прикидка", contract_total=150_000_000.0,
                      sections={"ar": 0.12}, section_names={}, amounts={}),
    ]
    payload = build_payload(
        base=base, sections=sections, overheads=[], analogs=analogs, all_projects=analogs
    )
    # невзвешенная медиана дала бы 11%; близкий по масштабу аналог тянет к 5%
    assert payload["lines"][0]["share_pct"] == 5.0


def test_plan_fact_correction_applied() -> None:
    base, sections, overheads, analogs = _base_inputs()
    plan_fact = {"pzu": (1.30, 4), "__all__": (1.10, 12)}
    payload = build_payload(
        base=base, sections=sections, overheads=overheads, analogs=analogs,
        all_projects=analogs, plan_fact=plan_fact,
    )
    pzu = payload["lines"][0]
    # медиана 3% от 10 млн = 300к, факт-коэффициент 1.30 -> 390к
    assert pzu["amount"] == 390_000.0
    assert "план/факт бюро ×1.30" in pzu["note"]
    assert any("план/факт" in w for w in payload["warnings"])
    # ar (аналоговый) корректируется общим коэффициентом 1.10
    ar = payload["lines"][1]
    assert ar["amount"] == round(500_000.0 * 1.10, 2)


def test_totals_range_present() -> None:
    base, sections, overheads, analogs = _base_inputs()
    payload = build_payload(
        base=base, sections=sections, overheads=overheads, analogs=analogs, all_projects=analogs
    )
    rng = payload["totals_range"]
    assert rng is not None
    assert rng["n"] >= 3
    assert rng["p25"] <= rng["p75"]
    # после ручной правки диапазон честно сбрасывается
    state = {
        "lines": _editor_rows(payload, "lines"),
        "overheads": _editor_rows(payload, "overheads"),
    }
    state["lines"][0]["amount"] = 999_999.0
    state["lines"][0]["touched"] = "amount"
    edited = apply_editor_state(payload, state)
    assert "totals_range" not in edited


def test_expertise_rules() -> None:
    from tender_ingest.economics.expertise import assess_expertise

    # 44-ФЗ без драйверов -> бюджет -> гос обязательна
    assert assess_expertise({}, "44-ФЗ")["verdict"] == "state_required"
    # капремонт побеждает всё
    kap = assess_expertise({"drivers": {"kapremont": True}}, "44-ФЗ")
    assert kap["verdict"] == "not_required"
    # малый нежилой объект без опасности
    small = assess_expertise(
        {"drivers": {"object_use": "nonresidential", "floors": 2, "area_m2": 1200,
                     "budget_funded": False}},
        "Коммерческие",
    )
    assert small["verdict"] == "not_required"
    # коммерческий без оснований для гос -> негос допустима
    nongov = assess_expertise({"drivers": {"budget_funded": False}}, "Коммерческие")
    assert nongov["verdict"] == "nongov_allowed"
    # ничего не известно -> честный unknown
    assert assess_expertise({}, None)["verdict"] == "unknown"
    # ОКН -> гос даже без бюджета
    okn = assess_expertise({"drivers": {"okn": True, "budget_funded": False}}, "Коммерческие")
    assert okn["verdict"] == "state_required"


def test_expertise_from_brief_text() -> None:
    """Без drivers режим подхватывается из текста поля «Госэкспертиза» брифа."""
    from tender_ingest.economics.expertise import assess_expertise

    state = assess_expertise(
        {"expertise": {"value": "Требуется прохождение государственной экспертизы ПД",
                       "quote": ""}},
        "Коммерческие",
    )
    assert state["verdict"] == "state_required"
    assert any("из текста ТЗ" in r for r in state["reasons"])

    nongov = assess_expertise(
        {"expertise": {"value": "Допускается негосударственная экспертиза по выбору",
                       "quote": ""}},
        "Коммерческие",
    )
    assert nongov["verdict"] == "nongov_allowed"

    none_ = assess_expertise(
        {"expertise": {"value": "Экспертиза проектной документации не требуется", "quote": ""}},
        "Коммерческие",
    )
    assert none_["verdict"] == "not_required"

    # «не указано» — не сигнал
    unknown = assess_expertise({"expertise": {"value": "не указано", "quote": ""}}, None)
    assert unknown["verdict"] == "unknown"

    # 44-ФЗ (бюджет) главнее текста про негос: гос + предупреждение о противоречии
    conflict = assess_expertise(
        {"expertise": {"value": "Негосударственная экспертиза", "quote": ""}}, "44-ФЗ"
    )
    assert conflict["verdict"] == "state_required"
    assert any("противоречие" in r for r in conflict["reasons"])


def test_sbcp_check_flags_deviation() -> None:
    base = BaseInput(nmck=10_000_000.0)
    sections = [
        SectionInput(name="АР", canon="ar"),
        SectionInput(name="КР", canon="kr"),
        SectionInput(name="ПЗ", canon="pz"),
    ]
    analogs = [
        _analog(1, {"ar": 0.10, "kr": 0.11, "pz": 0.10}),  # ПЗ аномально дорогой
        _analog(2, {"ar": 0.11, "kr": 0.12, "pz": 0.09}),
        _analog(3, {"ar": 0.09, "kr": 0.10, "pz": 0.11}),
    ]
    payload = build_payload(
        base=base, sections=sections, overheads=[], analogs=analogs, all_projects=analogs,
        object_kind="building", design_stage="pd",
    )
    check = payload["sbcp_check"]
    assert check is not None
    assert check["total_sbcp"] > 0
    # нормативные суммы проставлены построчно
    assert all(line.get("sbcp_amount") for line in payload["lines"])
    # ПЗ по СБЦП ~2% от базы, а у нас 10% -> сильное отклонение должно быть отмечено
    assert any("СБЦП" in w for w in payload["warnings"])


def test_aggregate_canons_match() -> None:
    from tender_ingest.economics.canon import detect_aggregate

    assert match_canon("Проектная документация (ПД)") == "pd_total"
    assert match_canon("Рабочая документация (РД)") == "rd_total"
    assert match_canon("Проектная и рабочая документация") == "pd_rd_total"
    assert match_canon("Предпроектная подготовка") == "ep"
    # регрессии: частные правила по-прежнему побеждают
    assert match_canon("Печать документации") == "print_docs"
    assert match_canon("Согласование документации") == "approvals"
    assert match_canon("Иная документация") == "other_docs"
    assert match_canon("ГЭ ПД") == "expertise_pd"
    assert match_canon("Раздел АР проектной документации") == "ar"
    assert match_canon("Сметная документация (СМ)") == "smeta"
    # detect_aggregate — узкий
    assert detect_aggregate("Проектная документация (ПД)") == "pd_total"
    assert detect_aggregate("РД") == "rd_total"
    assert detect_aggregate("Архитектурные решения") is None
    assert detect_aggregate("Экспертиза проектной документации") is None
    assert detect_aggregate("Сопровождение экспертизы ПД") is None


def _aggregate_case_analogs() -> list[AnalogProject]:
    """Аналоги с пораздельными АБСОЛЮТНЫМИ затратами (offer-режим, как в прод-кейсе)."""
    return [
        AnalogProject(id=1, sheet="work", title="A1", contract_total=8_000_000.0,
                      sections={}, section_names={},
                      amounts={"ar": 900_000, "kr": 700_000, "ios4": 400_000,
                               "smeta": 200_000, "pos": 300_000}),
        AnalogProject(id=2, sheet="work", title="A2", contract_total=9_000_000.0,
                      sections={}, section_names={},
                      amounts={"ar": 1_100_000, "kr": 800_000, "ios4": 500_000,
                               "smeta": 250_000, "pz": 150_000}),
        AnalogProject(id=3, sheet="work", title="A3", contract_total=7_000_000.0,
                      sections={}, section_names={},
                      amounts={"ar": 800_000, "kr": 600_000, "ios4": 350_000,
                               "smeta": 180_000, "pb": 120_000}),
    ]


def test_aggregate_derived_estimate_offer_mode() -> None:
    """Слепок прод-кейса: ПД/РД целиком + отдельные КМ/СМ -> производная оценка."""
    base = BaseInput(nmck=None)
    sections = [
        SectionInput(name="Проектная документация (ПД)", canon="pd_total"),
        SectionInput(name="Рабочая документация (РД)", canon="rd_total"),
        SectionInput(name="Раздел КМ", canon="kr"),
        SectionInput(name="Сметная документация", canon="smeta"),
    ]
    analogs = _aggregate_case_analogs()
    payload = build_payload(
        base=base, sections=sections, overheads=[], analogs=analogs, all_projects=analogs
    )
    pd, rd, kr_line, sm = payload["lines"]
    # kr и smeta заняты отдельными строками -> исключены из суммы design-группы:
    # D_1 = 900+400+300 = 1600к; D_2 = 1100+500+150 = 1750к; D_3 = 800+350+120 = 1270к
    # медиана D = 1600к -> ПД 640к (40%), РД 960к (60%)
    assert pd["source"] == "derived" and rd["source"] == "derived"
    assert pd["amount"] == 640_000.0
    assert rd["amount"] == 960_000.0
    assert pd["n_analogs"] == 3
    assert kr_line["source"] == "analog" and sm["source"] == "analog"
    # агрегаты вошли в итог, «занижен»-предупреждения нет
    assert payload["totals"]["cost"] > pd["amount"] + rd["amount"]
    assert not any("ЗАНИЖЕН" in w for w in payload["warnings"])


def test_aggregate_backstop_without_canon() -> None:
    """Главный тест прод-кейса: ИИ вернул canon=None, движок сам распознаёт агрегат."""
    base = BaseInput(nmck=None)
    sections = [SectionInput(name="Проектная документация (ПД)", canon=None)]
    analogs = _aggregate_case_analogs()
    payload = build_payload(
        base=base, sections=sections, overheads=[], analogs=analogs, all_projects=analogs
    )
    line = payload["lines"][0]
    assert line["canon"] == "pd_total"
    assert line["source"] == "derived"
    # ничего не занято: D = 2500к / 2800к / 2050к, медиана 2500к, ПД 40% = 1000к
    assert line["amount"] == 1_000_000.0


def test_aggregate_composite_exclusion() -> None:
    """Занят ar_kr -> у аналога исключаются и ar, и kr (обе стороны композита)."""
    base = BaseInput(nmck=None)
    sections = [
        SectionInput(name="ПД", canon="pd_total"),
        SectionInput(name="АР+КР", canon="ar_kr"),
    ]
    analogs = _aggregate_case_analogs()
    payload = build_payload(
        base=base, sections=sections, overheads=[], analogs=analogs, all_projects=analogs
    )
    pd = payload["lines"][0]
    # без ar и kr: D_1 = 400+200+300 = 900к; D_2 = 500+250+150 = 900к; D_3 = 350+180+120 = 650к
    assert pd["amount"] == round(0.4 * 900_000, 2)


def test_aggregate_sparse_analog_skipped_and_duplicate_guard() -> None:
    base = BaseInput(nmck=None)
    sections = [
        SectionInput(name="ПД", canon="pd_total"),
        SectionInput(name="Проектная документация повторно", canon="pd_total"),
    ]
    sparse = AnalogProject(id=9, sheet="work", title="Разреженный", contract_total=5e6,
                           sections={}, section_names={}, amounts={"ar": 500_000})
    analogs = [*_aggregate_case_analogs(), sparse]
    payload = build_payload(
        base=base, sections=sections, overheads=[], analogs=analogs, all_projects=analogs
    )
    first, second = payload["lines"]
    assert first["source"] == "derived" and first["n_analogs"] == 3  # разреженный не учтён
    assert second["source"] == "no_data"  # дубль агрегата остаётся без оценки
    assert any("повторяется" in w for w in payload["warnings"])
    # незакрытый агрегат — «итог занижен» первым предупреждением
    assert payload["warnings"][0].startswith("⚠ Итог ЗАНИЖЕН")


def test_aggregate_nmck_mode_shares() -> None:
    base = BaseInput(nmck=10_000_000.0)
    sections = [SectionInput(name="ПД и РД", canon="pd_rd_total")]
    analogs = [
        _analog(1, {"ar": 0.05, "kr": 0.04, "ios4": 0.03}),
        _analog(2, {"ar": 0.06, "kr": 0.05, "ios4": 0.02}),
        _analog(3, {"ar": 0.04, "kr": 0.05, "ios4": 0.04}),
    ]
    payload = build_payload(
        base=base, sections=sections, overheads=[], analogs=analogs, all_projects=analogs
    )
    line = payload["lines"][0]
    # доли: 0.12 / 0.13 / 0.13 -> медиана 0.13, фактор 1.0
    assert line["source"] == "derived"
    assert line["share_pct"] == 13.0
    assert line["amount"] == 1_300_000.0


def test_double_design_canon_warning() -> None:
    base, _sections, overheads, analogs = _base_inputs()
    sections = [
        SectionInput(name="АР (ПД)", canon="ar"),
        SectionInput(name="АР (РД)", canon="ar"),
    ]
    payload = build_payload(
        base=base, sections=sections, overheads=overheads, analogs=analogs,
        all_projects=analogs,
    )
    assert any("двойной счёт" in w for w in payload["warnings"])


def test_overhead_history_ranges_fallback() -> None:
    ranges = overhead_history_ranges([])  # пустая история -> страховочные диапазоны
    assert ranges["gip"] == (2.0, 15.0)
    lo, hi = ranges["reserve"]
    assert lo < hi


# --- режим предложения цены (НМЦК нет) ---


def _offer_inputs() -> tuple[
    BaseInput, list[SectionInput], list[OverheadInput], list[AnalogProject]
]:
    base = BaseInput(nmck=None, mode="offer")
    sections = [
        SectionInput(name="ПЗУ", canon="pzu"),
        SectionInput(name="АР", canon="ar"),
    ]
    overheads = [
        OverheadInput(canon="gip", pct=5.0),
        OverheadInput(canon="reserve", pct=5.0),
    ]
    analogs = [
        _analog(1, {}, {"pzu": 100_000.0, "ar": 200_000.0}),
        _analog(2, {}, {"pzu": 140_000.0, "ar": 260_000.0}),
        _analog(3, {}, {"pzu": 120_000.0}),
    ]
    return base, sections, overheads, analogs


def test_offer_mode_price_from_cost_and_margin() -> None:
    base, sections, overheads, analogs = _offer_inputs()
    payload = build_payload(
        base=base,
        sections=sections,
        overheads=overheads,
        analogs=analogs,
        all_projects=analogs,
        params=Params(target_margin_pct=40.0),
    )
    # суммы разделов — медианы абсолютных затрат аналогов
    assert payload["lines"][0]["amount"] == 120_000.0  # медиана 100/140/120
    assert payload["lines"][1]["amount"] == 230_000.0  # медиана 200/260
    cost_sections = 350_000.0
    # цена = себестоимость разделов / (1 − накладные 10% − маржа 40%)
    price = cost_sections / 0.5
    assert payload["totals"]["price"] == round(price, 2)
    assert payload["totals"]["mode"] == "offer"
    # накладные — процент от цены
    gip = next(o for o in payload["overheads"] if o["canon"] == "gip")
    assert gip["amount"] == round(0.05 * price, 2)
    # прибыль = маржа × цена
    assert abs(payload["totals"]["profit_at_offer"] - 0.4 * price) < 1
    # сетка по маржам и предупреждение о режиме
    assert all("margin_pct" in sc for sc in payload["scenarios"])
    assert any("НМЦК в тендере не указана" in w for w in payload["warnings"])


def test_offer_mode_apply_edits_and_target_margin() -> None:
    base, sections, overheads, analogs = _offer_inputs()
    payload = build_payload(
        base=base,
        sections=sections,
        overheads=overheads,
        analogs=analogs,
        all_projects=analogs,
        params=Params(target_margin_pct=40.0),
    )
    edited = apply_edits(payload, {"l0": {"amount": 200_000.0}}, target_margin_pct=30.0)
    assert edited["lines"][0]["amount"] == 200_000.0
    cost_sections = 200_000.0 + 230_000.0
    assert edited["totals"]["price"] == round(cost_sections / 0.6, 2)  # 10% накладные + 30% маржа
    assert edited["params"]["target_margin_pct"] == 30.0


# --- СБЦП (второй источник долей) ---


def test_sbcp_weights_normalized() -> None:
    for stage in ("pd", "rd", "pd_rd"):
        weights = sbcp_weights(stage)
        base_sum = sum(v for k, v in weights.items() if k not in ("ios2_3", "ar_kr"))
        assert abs(base_sum - 1.0) < 1e-9, stage
        # составные — суммы частей
        assert abs(weights["ios2_3"] - (weights["ios2"] + weights["ios3"])) < 1e-12
    pd = sbcp_weights("pd")
    # точные значения из таблицы 41 СБЦП ЖГС (проценты/100)
    assert abs(pd["ar"] - 0.14) < 1e-9
    assert abs(pd["odi"] - 0.02) < 1e-9
    assert abs(pd["oos"] - 0.07) < 1e-9
    # в РД нет ПЗ и ПОС
    rd = sbcp_weights("rd")
    assert "pz" not in rd and "pos" not in rd


def test_sbcp_fallback_scales_to_bureau_level() -> None:
    # аналоги дают якоря ar и kr; ios4 без аналогов -> оценка по СБЦП с коэффициентом k
    base = BaseInput(nmck=10_000_000.0)
    sections = [
        SectionInput(name="АР", canon="ar"),
        SectionInput(name="КР", canon="kr"),
        SectionInput(name="ОВиК", canon="ios4"),
    ]
    analogs = [
        _analog(1, {"ar": 0.04, "kr": 0.05}),
        _analog(2, {"ar": 0.04, "kr": 0.05}),
    ]
    payload = build_payload(
        base=base,
        sections=sections,
        overheads=[],
        analogs=analogs,
        all_projects=analogs,
        object_kind="building",
        design_stage="pd",
    )
    weights = sbcp_weights("pd")
    k_values = sorted([0.04 / weights["ar"], 0.05 / weights["kr"]])
    k = (k_values[0] + k_values[1]) / 2  # медиана двух якорей
    ovik = payload["lines"][2]
    assert ovik["source"] == "sbcp"
    assert ovik["amount"] == round(weights["ios4"] * k * 10_000_000.0, 2)
    assert "СБЦП" in ovik["note"]
    assert not payload["no_data"]  # раздел оценён нормативом, не потерян


def test_sbcp_not_applied_outside_buildings_or_without_anchors() -> None:
    base = BaseInput(nmck=1_000_000.0)
    sections = [SectionInput(name="ОВиК", canon="ios4"), SectionInput(name="АР", canon="ar")]
    analogs = [_analog(1, {"ar": 0.04}), _analog(2, {"ar": 0.05})]
    # благоустройство — СБЦП ЖГС не применяется
    landscaping = build_payload(
        base=base,
        sections=sections,
        overheads=[],
        analogs=analogs,
        all_projects=analogs,
        object_kind="landscaping",
        design_stage="pd",
    )
    assert landscaping["lines"][0]["source"] == "no_data"
    # здание, но якорь один (только ar) — фолбэк честно отключается с предупреждением
    building = build_payload(
        base=base,
        sections=sections,
        overheads=[],
        analogs=analogs,
        all_projects=analogs,
        object_kind="building",
        design_stage="pd",
    )
    assert building["lines"][0]["source"] == "no_data"
    assert any("СБЦП не применён" in w for w in building["warnings"])
