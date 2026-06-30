"""Оркестрация оценки релевантности: правила -> (maybe) арбитр -> upsert.

Читает закупки из очереди (status='pending'), считает score по правилам; спорные
(maybe) отдаёт LLM-арбитру, который превращает их в relevant/noise с обоснованием.
Результат — в `tender_relevance`, очередь двигается в 'scored'.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from tender_ingest.db.repository import RelevanceRepository
from tender_ingest.db.session import get_session_factory
from tender_ingest.relevance.arbiter import RelevanceArbiter, create_arbiter
from tender_ingest.relevance.rules import score_subject

log = structlog.get_logger()


@dataclass
class ScoreSummary:
    total: int
    relevant: int
    maybe_sent_to_llm: int
    noise: int


def score_pending(
    arbiter: RelevanceArbiter | None = None, limit: int | None = None
) -> ScoreSummary:
    """Оценить все закупки в очереди. Возвращает сводку по вердиктам."""
    arb = arbiter or create_arbiter()
    factory = get_session_factory()
    relevant = noise = sent = 0

    with factory() as session:
        repo = RelevanceRepository(session)
        rows = repo.pending(limit=limit)
        for reestr_number, subject in rows:
            result = score_subject(subject)
            decided_by = "rules"
            llm_reason: str | None = None
            verdict = result.verdict

            if verdict == "maybe":
                decision = arb.decide(subject or "")
                sent += 1
                decided_by = decision.provider
                llm_reason = decision.reason
                verdict = "relevant" if decision.relevant else "noise"

            if verdict == "relevant":
                relevant += 1
            else:
                noise += 1

            repo.upsert(
                reestr_number,
                score=result.score,
                verdict=verdict,
                decided_by=decided_by,
                matched=[{"label": m.label, "weight": m.weight} for m in result.matched],
                anti_matched=[{"label": m.label, "weight": m.weight} for m in result.anti_matched],
                llm_reason=llm_reason,
            )
        session.commit()
        total = len(rows)

    log.info(
        "score_done",
        total=total,
        relevant=relevant,
        maybe_sent_to_llm=sent,
        noise=noise,
        arbiter=arb.provider,
    )
    return ScoreSummary(total=total, relevant=relevant, maybe_sent_to_llm=sent, noise=noise)
