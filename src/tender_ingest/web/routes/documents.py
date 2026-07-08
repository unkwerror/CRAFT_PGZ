"""Документы по тендеру: загрузка, скачивание, удаление, разбор ТЗ через LLM."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response

from tender_ingest.config import get_settings
from tender_ingest.db.session import get_session_factory
from tender_ingest.documents.prompt import FIELD_LABELS
from tender_ingest.documents.report import build_analysis_pdf
from tender_ingest.web.doc_analysis_job import job as doc_job
from tender_ingest.web.progress import time_progress
from tender_ingest.web.repository import DocumentRepository, WebRepository
from tender_ingest.web.security import require_auth

router = APIRouter(dependencies=[Depends(require_auth)])


def _detail(reestr_number: str, msg: str | None = None) -> RedirectResponse:
    target = f"/tender/{reestr_number}"
    if msg:
        target += f"?{urlencode({'msg': msg})}"
    return RedirectResponse(target + "#docs", status_code=303)


@router.post("/tender/{reestr_number}/documents")
def upload_document(
    request: Request, reestr_number: str, file: Annotated[UploadFile, File()]
) -> RedirectResponse:
    name = (file.filename or "").strip() or "документ"
    data = file.file.read()
    limit = get_settings().doc_max_mb * 1024 * 1024
    if not data or len(data) > limit:
        return _detail(reestr_number)  # пусто/слишком большой — молча назад
    with get_session_factory()() as session:
        if WebRepository(session).get(reestr_number) is None:
            return _detail(reestr_number)
        DocumentRepository(session).add(reestr_number, name, file.content_type, data)
    return _detail(reestr_number)


@router.get("/tender/{reestr_number}/documents/{doc_id}")
def download_document(request: Request, reestr_number: str, doc_id: int) -> Response:
    with get_session_factory()() as session:
        doc = DocumentRepository(session).get(reestr_number, doc_id)
        if doc is None:
            return Response(status_code=404)
        ascii_name = quote(doc.filename)
        return Response(
            content=doc.data,
            media_type=doc.content_type or "application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{ascii_name}"},
        )


@router.post("/tender/{reestr_number}/documents/{doc_id}/delete")
def delete_document(request: Request, reestr_number: str, doc_id: int) -> RedirectResponse:
    with get_session_factory()() as session:
        DocumentRepository(session).delete(reestr_number, doc_id)
    return _detail(reestr_number)


@router.post("/tender/{reestr_number}/documents/{doc_id}/analyze")
def analyze_document(request: Request, reestr_number: str, doc_id: int) -> RedirectResponse:
    """Запустить разбор ТЗ в фоне: извлечение/распознавание -> Claude -> сохранение брифа.

    Скан-PDF Claude распознаёт сам (нативный document-блок), большой файл — частями,
    поэтому это минуты и идёт фоном (см. web/doc_analysis_job). Один разбор за раз.
    """
    with get_session_factory()() as session:
        if DocumentRepository(session).get(reestr_number, doc_id) is None:  # IDOR-safe
            return _detail(reestr_number, "Документ не найден")
    started = doc_job.start(doc_id)
    msg = (
        "Разбор ТЗ запущен в фоне — займёт до нескольких минут, обнови страницу позже"
        if started
        else "Уже идёт другой разбор ТЗ — дождитесь его завершения"
    )
    return _detail(reestr_number, msg)


@router.get("/tender/{reestr_number}/documents/{doc_id}/analysis-status")
def analysis_status(request: Request, reestr_number: str, doc_id: int) -> JSONResponse:
    """Прогресс разбора ИМЕННО этого документа (для прогресс-бара на карточке)."""
    s = doc_job.snapshot()
    running = s.running and s.doc_id == doc_id
    if not running:
        return JSONResponse({"running": False})
    progress, eta = time_progress(s.started_at, s.estimate_sec)
    return JSONResponse({"running": True, "progress": progress, "eta": eta, "phase": s.phase})


@router.get("/tender/{reestr_number}/documents/{doc_id}/analysis.pdf")
def download_analysis_pdf(request: Request, reestr_number: str, doc_id: int) -> Response:
    """Скачать PDF-отчёт по разбору ТЗ (собран из брифа Claude)."""
    with get_session_factory()() as session:
        docs = DocumentRepository(session)
        doc = docs.get(reestr_number, doc_id)  # IDOR-safe
        if doc is None:
            return Response(status_code=404)
        analysis = docs.latest_analyses_for(reestr_number).get(doc_id)
        if analysis is None:
            return Response(status_code=404)
        found = WebRepository(session).get(reestr_number)
        subject = found[0].subject if found else None
        pdf = build_analysis_pdf(
            brief=analysis.brief,
            filename=doc.filename,
            reestr=reestr_number,
            subject=subject,
            model=analysis.model,
            created_at=analysis.created_at,
            field_labels=FIELD_LABELS,
        )
        stem = Path(doc.filename).stem or "тз"
    name = quote(f"Разбор_{stem}.pdf")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{name}"},
    )
