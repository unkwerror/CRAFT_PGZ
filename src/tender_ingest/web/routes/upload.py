"""Загрузка Excel-выгрузки через веб: сохранить -> ingest. Оценка — отдельной кнопкой."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse

from tender_ingest.config import get_settings
from tender_ingest.pipeline import ingest_excel
from tender_ingest.web.security import require_auth
from tender_ingest.web.templating import templates

log = structlog.get_logger()
router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "upload.html", {"result": None, "error": None})


@router.post("/upload", response_class=HTMLResponse)
def upload(request: Request, file: Annotated[UploadFile, File()]) -> HTMLResponse:
    settings = get_settings()
    name = file.filename or ""
    if not name.lower().endswith(".xlsx"):
        return _error(request, "Нужен файл .xlsx (выгрузка из Контур.Закупок)")

    data = file.file.read()
    limit = settings.max_upload_mb * 1024 * 1024
    if len(data) > limit:
        return _error(request, f"Файл больше {settings.max_upload_mb} МБ")
    if not data:
        return _error(request, "Пустой файл")

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=True) as tmp:
        tmp.write(data)
        tmp.flush()
        try:
            ingest = ingest_excel(Path(tmp.name))
        except Exception as exc:  # noqa: BLE001 — показываем пользователю любую ошибку парсинга
            log.warning("web_upload_failed", filename=name, error=str(exc))
            return _error(request, f"Не удалось разобрать файл: {exc}")

    log.info("web_upload_done", filename=name, rows=ingest.rows_total)
    return templates.TemplateResponse(
        request,
        "upload.html",
        {"result": {"filename": name, "ingest": ingest}, "error": None},
    )


def _error(request: Request, message: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "upload.html", {"result": None, "error": message}, status_code=400
    )
