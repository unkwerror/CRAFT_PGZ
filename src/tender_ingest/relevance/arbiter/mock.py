"""MockArbiter — детерминированный арбитр для dev/тестов, без сети и ключей.

Эвристика: спорную закупку считаем релевантной, если в предмете есть явный
«проектный» сигнал. Это делает Mock осмысленным и предсказуемым в тестах.
"""

from __future__ import annotations

import re

from tender_ingest.relevance.arbiter.base import ArbiterVerdict, RelevanceArbiter

_SIGNAL = re.compile(
    r"проект|изыскат|реставрац|благоустройств|документац|планировк|архитектурн|\bпир\b",
    re.IGNORECASE,
)


class MockArbiter(RelevanceArbiter):
    provider = "mock"

    def decide(self, subject: str) -> ArbiterVerdict:
        if _SIGNAL.search(subject or ""):
            return ArbiterVerdict(
                relevant=True,
                reason="mock: найден проектный сигнал в предмете",
                provider=self.provider,
            )
        return ArbiterVerdict(
            relevant=False,
            reason="mock: проектного сигнала не найдено",
            provider=self.provider,
        )
