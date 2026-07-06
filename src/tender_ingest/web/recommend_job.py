"""Фоновый расчёт рекомендации ИИ-экономиста (по паттерну doc_analysis_job).

Один расчёт за раз (один воркер uvicorn). Сессия БД короткая: собрали корпус кейсов ->
закрыли -> вызов Claude без открытой транзакции -> открыли сессию только для записи.
"""

from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass

import structlog

from tender_ingest.db.session import get_session_factory
from tender_ingest.economics import build_case_corpus, create_economics_advisor
from tender_ingest.web.tracking import TrackingRepository

log = structlog.get_logger()


@dataclass(frozen=True)
class EconJobSnapshot:
    running: bool
    reestr_number: str | None
    message: str
    error: str | None
    finished_at: dt.datetime | None


class RecommendationJob:
    """Синглтон фонового расчёта рекомендации (один тендер за раз)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._reestr: str | None = None
        self._message = ""
        self._error: str | None = None
        self._finished_at: dt.datetime | None = None

    def snapshot(self) -> EconJobSnapshot:
        with self._lock:
            return EconJobSnapshot(
                self._running, self._reestr, self._message, self._error, self._finished_at
            )

    def start(self, reestr_number: str) -> bool:
        """Запустить расчёт. False — если уже идёт другой."""
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._reestr = reestr_number
            self._message = ""
            self._error = None
            self._finished_at = None
        threading.Thread(target=self._run, args=(reestr_number,), daemon=True).start()
        return True

    def _finish(self, message: str, error: str | None = None) -> None:
        with self._lock:
            self._running = False
            self._message = message
            self._error = error
            self._finished_at = dt.datetime.now(dt.UTC)

    def _run(self, reestr_number: str) -> None:
        try:
            # 1) короткая сессия: корпус кейсов (SQL-отбор похожих + агрегаты + фидбек)
            with get_session_factory()() as session:
                corpus = build_case_corpus(session, reestr_number)
            if corpus is None:
                self._finish("Тендер не найден")
                return

            # 2) вызов Claude без открытой сессии
            advisor = create_economics_advisor()
            recommendation = advisor.recommend(corpus)
            recommendation["n_cases"] = corpus.n_cases

            # 3) короткая сессия: сохраняем рекомендацию
            with get_session_factory()() as session:
                TrackingRepository(session).add_recommendation(
                    reestr_number, model=advisor.model, recommendation=recommendation
                )
            self._finish("Рекомендация экономиста готова")
        except ValueError as exc:
            self._finish("Не задан ключ ИИ — экономист недоступен", error=str(exc))
        except Exception as exc:  # noqa: BLE001 — фон: не роняем поток, ошибку показываем в UI
            log.exception("economics_failed", reestr_number=reestr_number)
            self._finish("Не удалось рассчитать экономику — подробности в логах", error=str(exc))


job = RecommendationJob()
