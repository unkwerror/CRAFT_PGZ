"""Оркестрация расчёта экономики тендера: бриф ТЗ -> proposer (LLM) -> engine -> БД.

Предусловия: по тендеру есть разобранное ТЗ (бриф) и импортирована база «Экономики».
ТЗ через ИИ повторно не прогоняется — состав работ берётся из брифа (work_breakdown).
Опция deep=True добавляет в вызов proposer полный текст ТЗ (точнее, дороже); для
скан-PDF без текстового слоя deep недоступен — считаем по брифу с предупреждением.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from tender_ingest.db.session import get_session_factory
from tender_ingest.documents.extract import UnsupportedDocumentError, extract_text
from tender_ingest.documents.prompt import build_context
from tender_ingest.economics.engine import Params, build_payload, overhead_history_ranges
from tender_ingest.economics.proposer import create_economics_proposer
from tender_ingest.economics.reviewer import create_economics_reviewer
from tender_ingest.economics.store import EconomicsStore
from tender_ingest.web.repository import DocumentRepository, WebRepository

log = structlog.get_logger()


class EconomicsPreconditionError(Exception):
    """Расчёт невозможен: нет брифа/базы знаний/цены. Сообщение показывается в UI."""


@dataclass(frozen=True)
class _TenderContext:
    card_context: str
    brief: dict[str, Any]
    nmck: float
    doc_id: int
    doc_bytes: bytes | None
    doc_filename: str
    doc_content_type: str | None


def _load_context(reestr_number: str, *, deep: bool) -> _TenderContext:
    with get_session_factory()() as session:
        found = WebRepository(session).get(reestr_number)
        if found is None:
            raise EconomicsPreconditionError("Тендер не найден")
        tender, relevance = found
        if tender.nmck is None:
            raise EconomicsPreconditionError(
                "У тендера нет НМЦК — без цены расчёт долей невозможен"
            )
        docs = DocumentRepository(session)
        analyses = docs.latest_analyses_for(reestr_number)
        if not analyses:
            raise EconomicsPreconditionError(
                "Сначала разберите ТЗ («Разобрать ТЗ» на документе) — расчёт идёт по брифу"
            )
        analysis = max(analyses.values(), key=lambda a: a.created_at)
        doc = docs.get(reestr_number, analysis.document_id)
        return _TenderContext(
            card_context=build_context(tender, relevance),
            brief=dict(analysis.brief),
            nmck=float(tender.nmck),
            doc_id=analysis.document_id,
            doc_bytes=bytes(doc.data) if deep and doc is not None else None,
            doc_filename=doc.filename if doc is not None else "",
            doc_content_type=doc.content_type if doc is not None else None,
        )


def _deep_text(ctx: _TenderContext) -> tuple[str | None, str | None]:
    """Полный текст ТЗ для deep-режима. -> (текст, предупреждение)."""
    if ctx.doc_bytes is None:
        return None, None
    try:
        extracted = extract_text(ctx.doc_filename, ctx.doc_content_type, ctx.doc_bytes)
    except UnsupportedDocumentError:
        return None, "Глубокий режим: формат ТЗ не поддерживает извлечение текста."
    if extracted.kind == "pdf" and extracted.low_text:
        return None, ("Глубокий режим недоступен: ТЗ — скан без текстового слоя, расчёт по брифу.")
    if not extracted.text.strip():
        return None, "Глубокий режим: не удалось извлечь текст ТЗ, расчёт по брифу."
    return extracted.text, None


def calculate_economics(reestr_number: str, *, deep: bool = False) -> dict[str, Any]:
    """Полный цикл расчёта: возвращает payload и сохраняет его в tender_economics."""
    ctx = _load_context(reestr_number, deep=deep)

    with get_session_factory()() as session:
        store = EconomicsStore(session)
        all_projects = store.analog_projects()
    if not all_projects:
        raise EconomicsPreconditionError(
            "База «Экономики» пуста — импортируйте файл: tender economics-import --file …"
        )

    deep_text, deep_warning = _deep_text(ctx)
    ranges = overhead_history_ranges(all_projects)
    proposer = create_economics_proposer()
    proposal = proposer.propose(
        card_context=ctx.card_context,
        brief=ctx.brief,
        nmck=ctx.nmck,
        projects=all_projects,
        overhead_ranges=ranges,
        deep_text=deep_text,
    )

    by_id = {p.id: p for p in all_projects}
    analogs = [by_id[i] for i in proposal.analog_ids if i in by_id]
    if not analogs:
        raise EconomicsPreconditionError(
            "ИИ не нашёл ни одного проекта-аналога в базе — расчёт по медианам невозможен"
        )

    payload = build_payload(
        base=proposal.base,
        sections=proposal.sections,
        overheads=proposal.overheads,
        analogs=analogs,
        all_projects=all_projects,
        analog_reasons=proposal.analog_reasons,
        params=Params(),
        comments=proposal.comments,
    )
    if deep_warning:
        payload["warnings_static"] = [deep_warning, *payload.get("warnings_static", [])]
        payload["warnings"] = [deep_warning, *payload.get("warnings", [])]
    payload["source_doc_id"] = ctx.doc_id
    payload["deep"] = bool(deep_text)

    # ИИ-ревью готового расчёта (открытые источники, веб-поиск). Не фатально:
    # упало — расчёт сохраняем без ревью, с предупреждением.
    try:
        reviewer = create_economics_reviewer()
        payload["review"] = reviewer.review(
            payload=payload,
            card_context=ctx.card_context,
            brief=ctx.brief,  # весь бриф: поля, findings, work_breakdown с цитатами
        )
        payload["review_model"] = reviewer.model
    except Exception:  # noqa: BLE001 — ревью вторично, расчёт важнее
        log.exception("economics_review_failed", reestr=reestr_number)
        note = "ИИ-ревью не удалось — расчёт без сверки с открытыми источниками."
        payload["warnings_static"] = [*payload.get("warnings_static", []), note]
        payload["warnings"] = [*payload.get("warnings", []), note]

    with get_session_factory()() as session:
        EconomicsStore(session).add_calculation(
            reestr_number, created_by="ai", model=proposer.model, payload=payload
        )
    log.info(
        "economics_calculated",
        reestr=reestr_number,
        analogs=len(analogs),
        lines=len(payload["lines"]),
        cost=payload["totals"]["cost"],
        deep=bool(deep_text),
    )
    return payload
