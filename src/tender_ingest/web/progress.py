"""Оценка прогресса фоновых джоб для прогресс-баров в UI.

Точной длительности LLM-вызовов не существует — показываем «примерно»: прогресс
по времени относительно оценки длительности (estimate_sec), с фазовой нижней
границей (фаза «ревью» не может быть меньше 60%). Если прогон затянулся дольше
оценки — бар замирает у 97%, а ETA показывает небольшой остаток, не ноль.
"""

from __future__ import annotations

import datetime as dt

_CEIL = 0.97  # бар не доходит до 100%, пока джоба реально не завершилась
_STALL_ETA_SEC = 15  # прогон дольше оценки: показываем «почти готово», не 0


def time_progress(
    started_at: dt.datetime | None,
    estimate_sec: float,
    min_fraction: float = 0.03,
) -> tuple[int, int]:
    """(процент 0–97, ETA сек) по прошедшему времени относительно оценки длительности."""
    if started_at is None:
        return int(min_fraction * 100), int(estimate_sec)
    elapsed = (dt.datetime.now(dt.UTC) - started_at).total_seconds()
    fraction = max(min_fraction, min(elapsed / max(estimate_sec, 1.0), _CEIL))
    remaining = estimate_sec - elapsed
    eta = int(remaining) if remaining > _STALL_ETA_SEC else _STALL_ETA_SEC
    return int(fraction * 100), eta
