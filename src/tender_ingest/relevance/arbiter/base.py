"""Интерфейс LLM-арбитра: по тексту карточки — score 0–100 и краткое резюме."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

# Маркер: карточку модель пропустила (её нет в ответе батча) — повод для ретрая.
NOT_SCORED = "не оценено моделью"


class ArbiterVerdict(BaseModel):
    score: int  # 0–100, выше — лучше подходит компании
    summary: str  # краткое резюме (резюме под тендер)
    provider: str


class RelevanceArbiter(ABC):
    """Адаптер LLM-арбитра. Реализация: ClaudeArbiter."""

    provider: str

    @abstractmethod
    def decide_batch(self, items: list[tuple[str, str]]) -> dict[str, ArbiterVerdict]:
        """Оценить пачку карточек [(reestr_number, card_text)] -> {reestr_number: verdict}."""
        raise NotImplementedError
