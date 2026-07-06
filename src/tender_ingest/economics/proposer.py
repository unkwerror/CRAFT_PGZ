"""LLM-шаг расчёта экономики: аналоги + маппинг разделов ТЗ + накладные. Один вызов.

ИИ здесь решает ТОЛЬКО семантические вопросы (никаких сумм и долей по разделам):
1) какие исторические проекты бюро похожи на тендер (по типу объекта и составу работ);
2) какому каноническому разделу соответствует каждая работа из ТЗ (или none);
3) какие проценты накладных уместны для типа проекта — в историческом диапазоне;
4) какова база расчёта (полная цена / доля на ПД) — по структуре контракта из ТЗ.

Вся арифметика — в engine.py; предложения ИИ по накладным дополнительно клампятся.
Вход — уже готовый бриф по ТЗ (work_breakdown), то есть ТЗ через ИИ второй раз НЕ
прогоняется; опция deep_text добавляет полный текст ТЗ для максимальной точности.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import anthropic
import structlog

from tender_ingest.config import Settings, get_settings
from tender_ingest.economics.canon import CATALOG
from tender_ingest.economics.engine import (
    OVERHEAD_KEYS,
    BaseInput,
    OverheadInput,
    SectionInput,
)
from tender_ingest.economics.store import AnalogProject, contract_scale_note

log = structlog.get_logger()

_MAX_TOKENS = 8000
_SHEET_LABEL = {"work": "факт", "preliminary": "прикидка"}

_CANON_KEYS = [s.key for s in CATALOG]

SYSTEM_PROMPT = """Ты — экономист проектного бюро «КРАФТ ГРУПП» (архитектурное \
проектирование, ПД/РД, благоустройство, ОКН). Тебе дают карточку тендера, бриф по ТЗ \
(составленный ИИ на предыдущем шаге, с составом работ work_breakdown) и базу знаний — \
реальные расчёты бюро по прошлым проектам (разделы и их доли от цены договора).

ТВОЯ ЗАДАЧА — только семантические решения. Деньги и доли разделов считает алгоритм \
по выбранным тобой аналогам, поэтому НЕ оценивай стоимость разделов и не подгоняй \
ничего под ответ.

1. analogs — выбери 3–7 НАИБОЛЕЕ похожих проектов из базы знаний: тот же тип объекта \
(благоустройство/здание/ОКН/линейный), сопоставимый состав работ (стадии, изыскания, \
ОКН-работы), затем сопоставимый масштаб цены. Если НМЦК не указана (закрытый тендер), \
масштаб оценивай по ТЗ (площадь, состав работ) — алгоритм возьмёт из аналогов \
АБСОЛЮТНЫЕ суммы затрат и сформирует цену предложения, поэтому похожесть масштаба \
особенно важна. Для каждого — короткое reason.
2. sections — перенеси КАЖДУЮ работу из work_breakdown брифа (если он пуст — собери \
состав работ из полей брифа doc_sections и findings, СТРОГО по их тексту). Для каждой \
подбери канонический раздел canon из каталога; если соответствия нет — canon = "none". \
Ничего не добавляй от себя и не объединяй позиции. quote — цитата из брифа/ТЗ.
3. overheads — процент (от цены) для накладных бюро: gip, project_manager, \
freelance_check, reserve. Выбирай ВНУТРИ исторического диапазона (дан в сообщении) \
по сложности и типу объекта (ОКН/большой состав разделов -> ближе к верхней границе; \
простое благоустройство -> к нижней). rationale — почему.
4. base — база расчёта: если из ТЗ/брифа видно, что цена контракта покрывает не только \
проектирование (например СМР + ПД, или на ПД отводится доля цены) — mode="pd_share" и \
pd_share_pct (доля цены на проектные работы, в процентах) с цитатой; иначе mode="full". \
Не выдумывай долю без основания в тексте.
5. object_kind — тип объекта: building (здание/сооружение ЖГС), landscaping \
(благоустройство/парк/сквер/трассы), heritage (ОКН/реставрация), linear (линейный \
объект/сети), other. Для building алгоритм дополнительно использует нормативные веса \
СБЦП по разделам без аналогов.
6. design_stage — стадия по ТЗ: pd (только проектная документация), rd (только \
рабочая), pd_rd (обе или не ясно).
7. comments — 2–4 предложения: главные экономические риски тендера и что стоит \
уточнить вручную (разделы без аналогов, нестандартные работы, изыскания).

Отвечай на русском. Не выдумывай данных, которых нет во входе."""


def _proposal_schema() -> dict[str, Any]:
    canon_enum = [*_CANON_KEYS, "none"]
    return {
        "type": "object",
        "properties": {
            "analogs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "reason": {"type": "string"},
                    },
                    "required": ["id", "reason"],
                    "additionalProperties": False,
                },
            },
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "canon": {"type": "string", "enum": canon_enum},
                        "quote": {"type": "string"},
                        "note": {"type": "string"},
                    },
                    "required": ["name", "canon", "quote", "note"],
                    "additionalProperties": False,
                },
            },
            "overheads": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "canon": {"type": "string", "enum": list(OVERHEAD_KEYS)},
                        "pct": {"type": "number"},
                        "rationale": {"type": "string"},
                    },
                    "required": ["canon", "pct", "rationale"],
                    "additionalProperties": False,
                },
            },
            "base": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["full", "pd_share"]},
                    "pd_share_pct": {"type": ["number", "null"]},
                    "rationale": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["mode", "pd_share_pct", "rationale", "quote"],
                "additionalProperties": False,
            },
            "object_kind": {
                "type": "string",
                "enum": ["building", "landscaping", "heritage", "linear", "other"],
            },
            "design_stage": {"type": "string", "enum": ["pd", "rd", "pd_rd"]},
            "comments": {"type": "string"},
        },
        "required": [
            "analogs",
            "sections",
            "overheads",
            "base",
            "object_kind",
            "design_stage",
            "comments",
        ],
        "additionalProperties": False,
    }


PROPOSAL_SCHEMA = _proposal_schema()


@dataclass(frozen=True)
class Proposal:
    base: BaseInput
    sections: list[SectionInput]
    overheads: list[OverheadInput]
    analog_ids: list[int]
    analog_reasons: dict[int, str]
    comments: str
    object_kind: str = "other"
    design_stage: str = "pd_rd"


def _project_line(project: AnalogProject) -> str:
    sections = ", ".join(
        f"{canon} {share * 100:.1f}%"
        for canon, share in sorted(project.sections.items(), key=lambda kv: -kv[1])
        if 0 < share < 0.9
    )
    return (
        f"#{project.id} [{_SHEET_LABEL.get(project.sheet, project.sheet)}] {project.title} — "
        f"цена {contract_scale_note(project.contract_total)} — разделы: {sections or 'нет данных'}"
    )


def build_message(
    *,
    card_context: str,
    brief: dict[str, Any],
    nmck: float | None,
    projects: list[AnalogProject],
    overhead_ranges: dict[str, tuple[float, float]],
    deep_text: str | None = None,
) -> str:
    catalog_lines = "\n".join(f"{s.key} — {s.label} [{s.group}]" for s in CATALOG)
    project_lines = "\n".join(_project_line(p) for p in projects)
    ranges_lines = "\n".join(
        f"{key}: {lo:.1f}–{hi:.1f}%" for key, (lo, hi) in overhead_ranges.items()
    )
    nmck_note = (
        f"НМЦК для справки: {nmck:,.0f} ₽.".replace(",", " ")
        if nmck is not None
        else "НМЦК НЕ УКАЗАНА — компания формирует цену предложения сама: аналоги "
        "выбирай строго сопоставимого масштаба по ТЗ."
    )
    parts = [
        "Подбери аналоги, сопоставь разделы работ и накладные для расчёта экономики "
        f"тендера. {nmck_note}",
        "=== КАРТОЧКА ЗАКУПКИ ===\n" + card_context,
        "=== БРИФ ПО ТЗ (разбор ИИ, источник состава работ) ===\n"
        + json.dumps(brief, ensure_ascii=False),
        "=== КАНОНИЧЕСКИЕ РАЗДЕЛЫ (ключ — название [группа]) ===\n" + catalog_lines,
        "=== БАЗА ЗНАНИЙ: ПРОЕКТЫ БЮРО ===\n" + project_lines,
        "=== ИСТОРИЧЕСКИЙ ДИАПАЗОН НАКЛАДНЫХ (10–90 перцентиль, % от цены) ===\n" + ranges_lines,
    ]
    if deep_text:
        parts.append(
            "=== ПОЛНЫЙ ТЕКСТ ТЗ (для максимальной точности состава работ) ===\n" + deep_text
        )
    return "\n\n".join(parts)


class EconomicsProposer:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def propose(
        self,
        *,
        card_context: str,
        brief: dict[str, Any],
        nmck: float | None,
        projects: list[AnalogProject],
        overhead_ranges: dict[str, tuple[float, float]],
        deep_text: str | None = None,
    ) -> Proposal:
        message = build_message(
            card_context=card_context,
            brief=brief,
            nmck=nmck,
            projects=projects,
            overhead_ranges=overhead_ranges,
            deep_text=deep_text,
        )
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=_MAX_TOKENS,
            system=[
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
            ],
            messages=[{"role": "user", "content": message}],
            output_config={"format": {"type": "json_schema", "schema": PROPOSAL_SCHEMA}},
        )
        raw = "".join(getattr(block, "text", "") for block in resp.content)
        data: dict[str, Any] = json.loads(raw)
        usage = resp.usage
        log.info(
            "economics_proposed",
            model=self._model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read=getattr(usage, "cache_read_input_tokens", 0),
            deep=bool(deep_text),
        )
        return _parse_proposal(data, nmck)


def _parse_proposal(data: dict[str, Any], nmck: float | None) -> Proposal:
    known_ids = {int(a["id"]): str(a["reason"]) for a in data.get("analogs", [])}
    sections = [
        SectionInput(
            name=str(s["name"]),
            canon=None if s["canon"] == "none" else str(s["canon"]),
            quote=str(s.get("quote", "")),
            note=str(s.get("note", "")),
        )
        for s in data.get("sections", [])
    ]
    overheads = [
        OverheadInput(
            canon=str(o["canon"]), pct=float(o["pct"]), rationale=str(o.get("rationale", ""))
        )
        for o in data.get("overheads", [])
    ]
    base_data = data.get("base", {})
    pd_share = base_data.get("pd_share_pct")
    base = BaseInput(
        nmck=nmck,
        mode=str(base_data.get("mode", "full")),
        pd_share_pct=float(pd_share) if pd_share is not None else None,
        rationale=str(base_data.get("rationale", "")),
        quote=str(base_data.get("quote", "")),
    )
    return Proposal(
        base=base,
        sections=sections,
        overheads=overheads,
        analog_ids=list(known_ids),
        analog_reasons=known_ids,
        comments=str(data.get("comments", "")),
        object_kind=str(data.get("object_kind", "other")),
        design_stage=str(data.get("design_stage", "pd_rd")),
    )


def create_economics_proposer(settings: Settings | None = None) -> EconomicsProposer:
    """Собрать proposer из конфигурации. Без ключа -> ValueError."""
    cfg = settings or get_settings()
    if not cfg.anthropic_api_key:
        raise ValueError("Нужен ANTHROPIC_API_KEY для расчёта экономики (Claude)")
    return EconomicsProposer(api_key=cfg.anthropic_api_key, model=cfg.claude_model)
