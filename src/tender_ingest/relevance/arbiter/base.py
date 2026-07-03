"""Интерфейс LLM-арбитра: по тексту карточки — score 0–100 и краткое резюме."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

# Маркер: карточку модель пропустила (её нет в ответе батча) — повод для ретрая.
NOT_SCORED = "не оценено моделью"
# Балл для неоценённых: строго между MAYBE_THRESHOLD(35) и RELEVANT_THRESHOLD(60),
# чтобы неоценённая карточка попала в «возможно» (на глаза человеку), а не в «шум».
NOT_SCORED_SCORE = 45


class ArbiterVerdict(BaseModel):
    score: int  # 0–100, выше — лучше подходит компании
    summary: str  # краткий вывод-рекомендация (2–4 предложения)
    reasoning: str = ""  # развёрнутое обоснование по рубрике (рассуждение перед баллом)
    confidence: int = 0  # 0–100, насколько модель уверена в оценке
    red_flags: list[str] = Field(default_factory=list)  # риски/настораживающие факторы
    provider: str


class RelevanceArbiter(ABC):
    """Адаптер LLM-арбитра. Реализация: ClaudeArbiter."""

    provider: str

    @abstractmethod
    def decide_batch(self, items: list[tuple[str, str]]) -> dict[str, ArbiterVerdict]:
        """Оценить пачку карточек [(reestr_number, card_text)] -> {reestr_number: verdict}."""
        raise NotImplementedError
