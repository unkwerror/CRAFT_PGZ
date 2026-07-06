"""Фоновый разбор ТЗ: извлечение/распознавание + Claude, в отдельном потоке.

Скан-PDF Claude распознаёт своим движком (нативный document-блок), большой файл идёт
частями — это минуты, поэтому синхронно нельзя (таймаут nginx). Один разбор за раз
(один воркер uvicorn); карточка опрашивает статус и показывает прогресс.

Сессию БД держим короткой: забрали данные документа и контекст -> закрыли -> долгий
вызов Claude без открытой транзакции -> открыли сессию только чтобы сохранить бриф.

Закрытые тендеры (source='closed', карточки Контура нет): тот же разбор дополнительно
извлекает поля карточки из ТЗ (объект card в брифе) — заполняются ТОЛЬКО пустые поля
(ручной ввод приоритетен), после чего тендер встаёт в очередь скоринга.
"""

from __future__ import annotations

import datetime as dt
import re
import threading
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog

from tender_ingest.db.models import AnalysisQueue, Tender, TenderRelevance
from tender_ingest.db.session import get_session_factory
from tender_ingest.documents.analyzer import create_document_analyzer
from tender_ingest.documents.extract import UnsupportedDocumentError, extract_text
from tender_ingest.documents.prompt import build_context
from tender_ingest.web.repository import DocumentRepository, WebRepository

log = structlog.get_logger()

CLOSED_SOURCE = "closed"


def _card_str(card: dict[str, Any], key: str) -> str | None:
    value = str(card.get(key) or "").strip()
    return value or None


def fill_card_from_brief(session: Any, reestr_number: str, brief: dict[str, Any]) -> list[str]:
    """Дозаполнить ПУСТЫЕ поля карточки закрытого тендера из brief['card'].

    Возвращает список заполненных полей (для сообщения в UI). Ручной ввод не трогаем.
    После заполнения тендер ставится в очередь скоринга (если ещё не оценён).
    """
    card = brief.get("card")
    tender = session.get(Tender, reestr_number)
    if tender is None or not isinstance(card, dict):
        return []
    filled: list[str] = []

    def set_if_empty(attr: str, value: object, label: str) -> None:
        if value is not None and getattr(tender, attr) in (None, ""):
            setattr(tender, attr, value)
            filled.append(label)

    nmck_raw = card.get("nmck")
    if nmck_raw is not None and tender.nmck is None:
        try:
            tender.nmck = Decimal(str(nmck_raw))
            tender.currency = tender.currency or "RUB"
            filled.append("НМЦК")
        except InvalidOperation:
            pass
    set_if_empty("subject", _card_str(card, "subject"), "предмет")
    set_if_empty("customer_name", _card_str(card, "customer_name"), "заказчик")
    set_if_empty("customer_inn", _card_str(card, "customer_inn"), "ИНН")
    set_if_empty("region_name", _card_str(card, "region_name"), "регион")
    set_if_empty("delivery_place", _card_str(card, "delivery_place"), "место работ")
    set_if_empty("law", _card_str(card, "law"), "закон")
    set_if_empty("purchase_method", _card_str(card, "purchase_method"), "способ отбора")
    set_if_empty("advance_raw", _card_str(card, "advance"), "аванс")
    code = _card_str(card, "region_code")
    if code and re.fullmatch(r"\d{2}", code) and tender.region_code in (None, ""):
        tender.region_code = code
    deadline = _card_str(card, "submission_deadline")
    if deadline and tender.submission_deadline is None:
        try:
            tender.submission_deadline = dt.datetime.fromisoformat(deadline)
            filled.append("дедлайн")
        except ValueError:
            pass

    # Полный функционал как у контуровских: после заполнения карточки — в очередь скоринга.
    has_relevance = session.get(TenderRelevance, reestr_number) is not None
    if not has_relevance:
        queue = session.get(AnalysisQueue, reestr_number)
        if queue is None:
            session.add(AnalysisQueue(reestr_number=reestr_number, status="pending"))
        else:
            queue.status = "pending"
    session.commit()
    return filled


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
                extract_card = tender is not None and tender.source == CLOSED_SOURCE

            # 2) извлечение + разбор (без открытой сессии — это минуты)
            extracted = extract_text(filename, content_type, data)
            analyzer = create_document_analyzer()
            if extracted.kind == "pdf" and extracted.low_text:
                # скан без текстового слоя -> отдаём PDF Claude, он распознаёт сам
                brief = analyzer.analyze_pdf(data, context, extract_card)
                pages, truncated = (extracted.pages or None), False
            elif extracted.text.strip():
                brief = analyzer.analyze(extracted.text, context, extract_card)
                pages, truncated = (extracted.pages or None), extracted.truncated
            else:
                self._finish("Файл пуст или не удалось извлечь содержимое")
                return

            # 3) короткая сессия: сохраняем бриф (+карточка закрытого тендера из ТЗ)
            filled: list[str] = []
            with get_session_factory()() as session:
                DocumentRepository(session).add_analysis(
                    doc_id,
                    reestr,
                    model=analyzer.model,
                    brief=brief,
                    pages=pages,
                    truncated=truncated,
                )
                if extract_card:
                    filled = fill_card_from_brief(session, reestr, brief)
            message = f"ТЗ разобрано: {filename}"
            if filled:
                message += ". Карточка дополнена из ТЗ: " + ", ".join(filled)
            self._finish(message)
        except UnsupportedDocumentError:
            self._finish("Разбор поддерживает только PDF, DOCX и XLSX")
        except ValueError as exc:
            self._finish("Не задан ключ ИИ — разбор недоступен", error=str(exc))
        except Exception as exc:  # noqa: BLE001 — фон: не роняем поток, ошибку показываем в UI
            log.exception("doc_analysis_failed", doc_id=doc_id)
            self._finish("Не удалось разобрать ТЗ — подробности в логах сервера", error=str(exc))


job = DocAnalysisJob()
