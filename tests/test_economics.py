"""Тесты экономики: canon-матчер, парсер «Экономики», движок расчёта."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from tender_ingest.economics.canon import CATALOG_BY_KEY, match_canon
from tender_ingest.economics.engine import (
    BaseInput,
    OverheadInput,
    SectionInput,
    apply_edits,
    build_payload,
    overhead_history_ranges,
)
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


def _analog(pid: int, sections: dict[str, float]) -> AnalogProject:
    return AnalogProject(
        id=pid,
        sheet="work",
        title=f"Проект {pid}",
        contract_total=1_000_000.0,
        sections=sections,
        section_names={},
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


def test_overhead_history_ranges_fallback() -> None:
    ranges = overhead_history_ranges([])  # пустая история -> страховочные диапазоны
    assert ranges["gip"] == (2.0, 15.0)
    lo, hi = ranges["reserve"]
    assert lo < hi
