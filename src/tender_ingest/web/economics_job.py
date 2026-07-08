"""Фоновый расчёт экономики тендера (LLM + движок), в отдельном потоке.

Один расчёт за раз (один воркер uvicorn) — как и разбор ТЗ. Карточка опрашивает
статус и перезагружается по завершении. Долгий вызов Claude идёт без открытой
сессии БД (сервис сам открывает короткие сессии на чтение/запись).
"""

from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass

import structlog

from tender_ingest.config import MissingApiKeyError
from tender_ingest.economics.service import EconomicsPreconditionError, calculate_economics

log = structlog.get_logger()


@dataclass(frozen=True)
class EcoJobSnapshot:
    running: bool
    reestr_number: str | None
    message: str
    error: str | None
    finished_at: dt.datetime | None
    started_at: dt.datetime | None = None
    phase: str = ""
    phase_fraction: float = 0.0
    estimate_sec: float = 110.0


class EconomicsJob:
    """Синглтон фонового расчёта экономики (один тендер за раз)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._reestr: str | None = None
        self._message = ""
        self._error: str | None = None
        self._finished_at: dt.datetime | None = None
        self._started_at: dt.datetime | None = None
        self._phase = ""
        self._phase_fraction = 0.0
        self._estimate_sec = 110.0

    def snapshot(self) -> EcoJobSnapshot:
        with self._lock:
            return EcoJobSnapshot(
                self._running,
                self._reestr,
                self._message,
                self._error,
                self._finished_at,
                self._started_at,
                self._phase,
                self._phase_fraction,
                self._estimate_sec,
            )

    def start(self, reestr_number: str, *, deep: bool) -> bool:
        """Запустить расчёт. False — если уже идёт другой расчёт."""
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._reestr = reestr_number
            self._message = ""
            self._error = None
            self._finished_at = None
            self._started_at = dt.datetime.now(dt.UTC)
            self._phase = "готовлю данные тендера"
            self._phase_fraction = 0.02
            # ~2 LLM-вызова: подбор аналогов + ревью с веб-поиском; deep читает всё ТЗ
            self._estimate_sec = 150.0 if deep else 110.0
        threading.Thread(target=self._run, args=(reestr_number, deep), daemon=True).start()
        return True

    def _set_phase(self, phase: str, fraction: float) -> None:
        with self._lock:
            self._phase = phase
            self._phase_fraction = fraction

    def _finish(self, message: str, error: str | None = None) -> None:
        with self._lock:
            self._running = False
            self._message = message
            self._error = error
            self._finished_at = dt.datetime.now(dt.UTC)

    def _run(self, reestr_number: str, deep: bool) -> None:
        try:
            payload = calculate_economics(reestr_number, deep=deep, on_phase=self._set_phase)
            cost = payload["totals"]["cost"]
            self._finish(f"Расчёт экономики готов: себестоимость {cost:,.0f} ₽".replace(",", " "))
        except EconomicsPreconditionError as exc:
            self._finish(str(exc))
        except MissingApiKeyError as exc:
            self._finish("Не задан ключ ИИ — расчёт недоступен", error=str(exc))
        except Exception as exc:  # noqa: BLE001 — фон: не роняем поток, ошибку показываем в UI
            log.exception("economics_failed", reestr=reestr_number)
            self._finish(
                "Не удалось рассчитать экономику — подробности в логах сервера", error=str(exc)
            )


job = EconomicsJob()
