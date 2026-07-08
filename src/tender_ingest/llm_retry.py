"""Ретрай вызовов Claude на временных ошибках (CLAUDE.md: retry на 429/5xx с jitter).

SDK сам ретраит 429/5xx при УСТАНОВКЕ соединения, но ошибка, пришедшая уже ВНУТРИ
SSE-стрима (напр. 529 overloaded_error), поднимается как исключение без ретрая —
оборачиваем весь вызов (stream + get_final_message) целиком.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable

import anthropic
import structlog

log = structlog.get_logger()

# 4 попытки: паузы ~5с/15с/45с — переживаем эпизод перегрузки API длиной ~минуту
_ATTEMPTS = 4
_BASE_DELAY_SEC = 5.0


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, anthropic.APIConnectionError | anthropic.RateLimitError):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code == 429 or exc.status_code >= 500:
            return True
        # Ошибка ВНУТРИ SSE-стрима приходит при HTTP 200 — статус бесполезен,
        # тип смотрим в теле: {'error': {'type': 'overloaded_error', ...}}
        body = exc.body if isinstance(exc.body, dict) else {}
        error = body.get("error")
        err_type = error.get("type") if isinstance(error, dict) else None
        return err_type in ("overloaded_error", "api_error", "rate_limit_error")
    return False


def call_with_retries[T](fn: Callable[[], T], *, label: str) -> T:
    """Выполнить вызов Claude с ретраями: 429/5xx/сеть -> пауза с jitter и повтор."""
    for attempt in range(_ATTEMPTS):
        try:
            return fn()
        except Exception as exc:
            if not _retryable(exc) or attempt == _ATTEMPTS - 1:
                raise
            delay = _BASE_DELAY_SEC * (3**attempt) + random.uniform(0, 2)  # noqa: S311
            log.warning(
                "anthropic_retry",
                label=label,
                attempt=attempt + 1,
                delay_sec=round(delay, 1),
                error=str(exc)[:200],
            )
            time.sleep(delay)
    raise RuntimeError("unreachable")  # pragma: no cover
