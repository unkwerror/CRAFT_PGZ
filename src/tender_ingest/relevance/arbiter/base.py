"""Интерфейс арбитра: решает спорные случаи «релевантно / нет» с обоснованием."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel


class ArbiterVerdict(BaseModel):
    relevant: bool
    reason: str
    provider: str


class RelevanceArbiter(ABC):
    """Адаптер LLM-арбитра. Реализации: MockArbiter (dev), YandexGPTArbiter (prod)."""

    provider: str

    @abstractmethod
    def decide(self, subject: str) -> ArbiterVerdict:
        """Решить, релевантна ли закупка профилю архбюро, по тексту предмета."""
        raise NotImplementedError
