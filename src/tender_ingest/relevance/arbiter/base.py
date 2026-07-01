"""Интерфейс арбитра: решает спорные случаи «релевантно / нет» с обоснованием."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel


class ArbiterVerdict(BaseModel):
    relevant: bool
    reason: str
    provider: str


class RelevanceArbiter(ABC):
    """Интерфейс LLM-арбитра релевантности. Реализация: ClaudeArbiter."""

    provider: str

    @abstractmethod
    def decide(self, subject: str) -> ArbiterVerdict:
        """Решить, релевантна ли закупка профилю архбюро, по тексту предмета."""
        raise NotImplementedError
