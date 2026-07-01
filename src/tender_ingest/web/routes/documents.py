"""Документы по тендеру: загрузка, скачивание, удаление (для детального анализа)."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import RedirectResponse, Response

from tender_ingest.config import get_settings
from tender_ingest.db.session import get_session_factory
from tender_ingest.web.repository import DocumentRepository, WebRepository
from tender_ingest.web.security import require_auth

router = APIRouter(dependencies=[Depends(require_auth)])


def _detail(reestr_number: str) -> RedirectResponse:
    return RedirectResponse(f"/tender/{reestr_number}", status_code=303)


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
