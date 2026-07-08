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
