"""Фоновый ИИ-скоринг: гоняем в отдельном потоке, чтобы не держать HTTP-запрос.

На 1000+ карточках прогон идёт минуты — синхронный ответ упёрся бы в таймаут nginx
(504). Кнопка запускает поток и сразу возвращает управление; список опрашивает
/score/status и показывает прогресс.

Один воркер uvicorn -> состояние держим в модуле под локом, один прогон за раз.
Запись в БД идёт по батчу (см. scorer.score_pending) — при рестарте потока уже
оценённые карточки не теряются.
"""

from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass

import structlog

from tender_ingest.db.repository import RelevanceRepository
from tender_ingest.db.session import get_session_factory
from tender_ingest.relevance.scorer import score_pending

log = structlog.get_logger()


@dataclass(frozen=True)
class JobSnapshot:
    running: bool
    done: int
    total: int
    message: str
    error: str | None
    finished_at: dt.datetime | None
    started_at: dt.datetime | None = None


class ScoringJob:
    """Синглтон фонового скоринга. start() неблокирующий, snapshot() — для UI."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._done = 0
        self._total = 0
        self._message = ""
        self._error: str | None = None
        self._finished_at: dt.datetime | None = None
        self._started_at: dt.datetime | None = None

    def snapshot(self) -> JobSnapshot:
        with self._lock:
            return JobSnapshot(
                running=self._running,
                done=self._done,
                total=self._total,
                message=self._message,
                error=self._error,
                finished_at=self._finished_at,
                started_at=self._started_at,
            )

    def start(self, *, requeue: bool) -> bool:
        """Запустить фоновый прогон. False — если уже идёт (второй не плодим)."""
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._done = 0
            self._total = 0
            self._message = ""
            self._error = None
            self._finished_at = None
            self._started_at = dt.datetime.now(dt.UTC)
        threading.Thread(target=self._run, args=(requeue,), daemon=True).start()
        return True

    def _progress(self, done: int, total: int) -> None:
        with self._lock:
            self._done = done
            self._total = total

    def _run(self, requeue: bool) -> None:
        try:
            if requeue:
                with get_session_factory()() as session:
                    RelevanceRepository(session).requeue_all()
            summary = score_pending(progress_cb=self._progress)
            if summary.total == 0:
                msg = "Нечего оценивать — все закупки уже оценены"
            else:
                msg = (
                    f"Оценено {summary.total}: подходят {summary.relevant}, "
                    f"возможно {summary.maybe}, аукцион {summary.auction}, "
                    f"не подходят {summary.noise}"
                )
                if summary.skipped:
                    msg += f"; пропущено без ключа {summary.skipped}"
            with self._lock:
                self._message = msg
        except Exception as exc:  # noqa: BLE001 — фон: не роняем поток, ошибку показываем в UI
            log.exception("scoring_job_failed")
            with self._lock:
                self._error = str(exc)
                self._message = "Ошибка ИИ-скоринга — подробности в логах сервера"
        finally:
            with self._lock:
                self._running = False
                self._finished_at = dt.datetime.now(dt.UTC)


job = ScoringJob()  # один на процесс
