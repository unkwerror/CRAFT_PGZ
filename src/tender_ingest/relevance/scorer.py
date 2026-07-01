"""Оркестрация скоринга: факторы → жёсткие исключения → Claude → tender_relevance.

Для каждой закупки из очереди:
1. считаем объективные факторы (factors.py);
2. жёсткий дисквалификатор (аукцион/стоп-заказчик/СНГ/неактивный этап) → noise, без Claude;
3. иначе Claude по полной карточке даёт score 0–100 + резюме; вердикт — из score.

Вызовы Claude (узкое место — сеть) идут параллельно; запись в БД — однопоточная.
Тексты карточек собираем ДО пула, чтобы не трогать ORM-объекты из потоков.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import structlog

from tender_ingest.db.repository import RelevanceRepository
from tender_ingest.db.session import get_session_factory
from tender_ingest.relevance.arbiter import RelevanceArbiter, create_arbiter
from tender_ingest.relevance.arbiter.base import NOT_SCORED
from tender_ingest.relevance.arbiter.prompt import build_card_block
from tender_ingest.relevance.factors import Factors, compute_factors, hard_exclusion

log = structlog.get_logger()

RELEVANT_THRESHOLD = 60
MAYBE_THRESHOLD = 35
CHUNK_SIZE = 20  # карточек в одном запросе к Claude
CHUNK_CONCURRENCY = 6  # одновременных батч-запросов


def verdict_from_score(score: int) -> str:
    if score >= RELEVANT_THRESHOLD:
        return "relevant"
    if score >= MAYBE_THRESHOLD:
        return "maybe"
    return "noise"


@dataclass
class ScoreSummary:
    total: int
    relevant: int
    maybe: int
    noise: int
    sent_to_llm: int


@dataclass
class _Result:
    reestr_number: str
    score: int
    verdict: str
    summary: str
    decided_by: str
    factors: Factors


def score_pending(
    arbiter: RelevanceArbiter | None = None, limit: int | None = None
) -> ScoreSummary:
    """Оценить все закупки в очереди. Возвращает сводку по вердиктам."""
    arb = arbiter or create_arbiter()
    factory = get_session_factory()

    with factory() as session:
        repo = RelevanceRepository(session)
        tenders = repo.pending(limit=limit)

        # Фаза 1 (главный поток): факторы, жёсткие исключения, тексты карточек.
        results: dict[str, _Result] = {}
        llm_jobs: list[tuple[str, str, Factors]] = []  # (reestr, card_text, factors)
        for t in tenders:
            f = compute_factors(t)
            hard = hard_exclusion(f)
            if hard is not None:
                results[t.reestr_number] = _Result(t.reestr_number, 0, "noise", hard, "rules", f)
            else:
                llm_jobs.append((t.reestr_number, build_card_block(t), f))

        # Фаза 2 (пул потоков): Claude оценивает карточки пачками, батчи — параллельно.
        def _score_chunk(chunk: list[tuple[str, str, Factors]]) -> list[_Result]:
            verdicts = arb.decide_batch([(reestr, card) for reestr, card, _ in chunk])
            out = []
            for reestr, _, factors in chunk:
                v = verdicts[reestr]
                out.append(
                    _Result(
                        reestr,
                        v.score,
                        verdict_from_score(v.score),
                        v.summary,
                        arb.provider,
                        factors,
                    )
                )
            return out

        if llm_jobs:
            chunks = [llm_jobs[i : i + CHUNK_SIZE] for i in range(0, len(llm_jobs), CHUNK_SIZE)]
            workers = min(CHUNK_CONCURRENCY, len(chunks))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for chunk_results in pool.map(_score_chunk, chunks):
                    for r in chunk_results:
                        results[r.reestr_number] = r

            # Ретрай карточек, которые модель пропустила в батче (одним запросом).
            missed = [job for job in llm_jobs if results[job[0]].summary == NOT_SCORED]
            if missed:
                log.info("score_retry", missed=len(missed))
                for r in _score_chunk(missed):
                    if r.summary != NOT_SCORED:
                        results[r.reestr_number] = r

        # Фаза 3 (главный поток): запись.
        counts = {"relevant": 0, "maybe": 0, "noise": 0}
        for t in tenders:
            r = results[t.reestr_number]
            counts[r.verdict] += 1
            repo.upsert(
                r.reestr_number,
                score=r.score,
                verdict=r.verdict,
                decided_by=r.decided_by,
                summary=r.summary,
                factors=r.factors.as_dict(),
            )
        session.commit()
        total = len(tenders)

    log.info(
        "score_done",
        total=total,
        relevant=counts["relevant"],
        maybe=counts["maybe"],
        noise=counts["noise"],
        sent_to_llm=len(llm_jobs),
        arbiter=arb.provider,
    )
    return ScoreSummary(
        total=total,
        relevant=counts["relevant"],
        maybe=counts["maybe"],
        noise=counts["noise"],
        sent_to_llm=len(llm_jobs),
    )
