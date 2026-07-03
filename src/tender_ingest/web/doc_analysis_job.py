"""Фоновый разбор ТЗ: извлечение/распознавание + Claude, в отдельном потоке.

Скан-PDF Claude распознаёт своим движком (нативный document-блок), большой файл идёт
частями — это минуты, поэтому синхронно нельзя (таймаут nginx). Один разбор за раз
(один воркер uvicorn); карточка опрашивает статус и показывает прогресс.

Сессию БД держим короткой: забрали данные документа и контекст -> закрыли -> долгий
вызов Claude без открытой транзакции -> открыли сессию только чтобы сохранить бриф.
"""

from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass

import structlog

from tender_ingest.db.session import get_session_factory
from tender_ingest.documents.analyzer import create_document_analyzer
from tender_ingest.documents.extract import UnsupportedDocumentError, extract_text
from tender_ingest.documents.prompt import build_context
from tender_ingest.web.repository import DocumentRepository, WebRepository

log = structlog.get_logger()


@dataclass(frozen=True)
class DocJobSnapshot:
    running: bool
    doc_id: int | None
    message: str
    error: str | None
    finished_at: dt.datetime | None


class DocAnalysisJob:
    """Синглтон фонового разбора ТЗ (один документ за раз)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._doc_id: int | None = None
        self._message = ""
        self._error: str | None = None
        self._finished_at: dt.datetime | None = None

    def snapshot(self) -> DocJobSnapshot:
        with self._lock:
            return DocJobSnapshot(
                self._running, self._doc_id, self._message, self._error, self._finished_at
            )

    def start(self, doc_id: int) -> bool:
        """Запустить разбор документа. False — если уже идёт другой разбор."""
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._doc_id = doc_id
            self._message = ""
            self._error = None
            self._finished_at = None
        threading.Thread(target=self._run, args=(doc_id,), daemon=True).start()
        return True

    def _finish(self, message: str, error: str | None = None) -> None:
        with self._lock:
            self._running = False
            self._message = message
            self._error = error
            self._finished_at = dt.datetime.now(dt.UTC)

    def _run(self, doc_id: int) -> None:
        try:
            # 1) короткая сессия: данные документа + контекст (карточка + скоринг)
            with get_session_factory()() as session:
                doc = DocumentRepository(session).get_by_id(doc_id)
                if doc is None:
                    self._finish("Документ не найден")
                    return
                filename = doc.filename
                content_type = doc.content_type
                data = bytes(doc.data)
                reestr = doc.reestr_number
                found = WebRepository(session).get(reestr)
                tender, relevance = found if found else (None, None)
                context = build_context(tender, relevance) if tender is not None else ""

            # 2) извлечение + разбор (без открытой сессии — это минуты)
            extracted = extract_text(filename, content_type, data)
            analyzer = create_document_analyzer()
            if extracted.kind == "pdf" and extracted.low_text:
                # скан без текстового слоя -> отдаём PDF Claude, он распознаёт сам
                brief = analyzer.analyze_pdf(data, context)
                pages, truncated = (extracted.pages or None), False
            elif extracted.text.strip():
                brief = analyzer.analyze(extracted.text, context)
                pages, truncated = (extracted.pages or None), extracted.truncated
            else:
                self._finish("Файл пуст или не удалось извлечь содержимое")
                return

            # 3) короткая сессия: сохраняем бриф
            with get_session_factory()() as session:
                DocumentRepository(session).add_analysis(
                    doc_id,
                    reestr,
                    model=analyzer.model,
                    brief=brief,
                    pages=pages,
                    truncated=truncated,
                )
            self._finish(f"ТЗ разобрано: {filename}")
        except UnsupportedDocumentError:
            self._finish("Разбор поддерживает только PDF, DOCX и XLSX")
        except ValueError as exc:
            self._finish("Не задан ключ ИИ — разбор недоступен", error=str(exc))
        except Exception as exc:  # noqa: BLE001 — фон: не роняем поток, ошибку показываем в UI
            log.exception("doc_analysis_failed", doc_id=doc_id)
            self._finish("Не удалось разобрать ТЗ — подробности в логах сервера", error=str(exc))


job = DocAnalysisJob()
