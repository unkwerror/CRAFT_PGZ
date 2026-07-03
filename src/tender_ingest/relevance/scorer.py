"""Оркестрация скоринга: факторы → жёсткие исключения → Claude → tender_relevance.

Для каждой закупки из очереди:
1. считаем объективные факторы (factors.py);
2. жёсткий дисквалификатор (аукцион/стоп-заказчик/СНГ/неактивный этап) → noise, без Claude;
3. иначе Claude по полной карточке даёт score 0–100 + резюме; вердикт — из score.

Вызовы Claude (узкое место — сеть) идут параллельно; запись в БД — однопоточная.
Тексты карточек собираем ДО пула, чтобы не трогать ORM-объекты из потоков.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import structlog

from tender_ingest.db.repository import BlacklistRepository, RelevanceRepository
from tender_ingest.db.session import get_session_factory
from tender_ingest.relevance.arbiter import RelevanceArbiter, create_arbiter
from tender_ingest.relevance.arbiter.base import NOT_SCORED
from tender_ingest.relevance.arbiter.prompt import build_card_block
from tender_ingest.relevance.factors import Factors, compute_factors, hard_exclusion, is_auction

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
    auction: int
    sent_to_llm: int
    skipped: int  # LLM-часть пропущена (нет ключа) — остались в очереди


@dataclass
class _Result:
    reestr_number: str
    score: int
    verdict: str
    summary: str
    decided_by: str
    factors: Factors
    reasoning: str | None = None
    confidence: int | None = None
    red_flags: list[str] | None = None


def score_pending(
    arbiter: RelevanceArbiter | None = None,
    limit: int | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> ScoreSummary:
    """Оценить все закупки в очереди. Возвращает сводку по вердиктам.

    Жёсткие исключения и аукционы решаются без LLM (даже без ключа). LLM-часть —
    best-effort: если арбитр недоступен, эти закупки остаются в очереди (pending).
    Пишем и коммитим ПО БАТЧУ: при рестарте фонового прогона уже оценённые (и
    оплаченные) карточки не теряются. progress_cb(done, total) — для UI прогресса.
    """
    factory = get_session_factory()
    skipped = 0

    with factory() as session:
        repo = RelevanceRepository(session)
        tenders = repo.pending(limit=limit)
        blacklist_inns = BlacklistRepository(session).all_inns()

        def _write(r: _Result) -> None:
            repo.upsert(
                r.reestr_number,
                score=r.score,
                verdict=r.verdict,
                decided_by=r.decided_by,
                summary=r.summary,
                factors=r.factors.as_dict(),
                reasoning=r.reasoning,
                confidence=r.confidence,
                red_flags=r.red_flags,
            )

        # Фаза 1 (без LLM): факторы, жёсткие исключения, аукционы — пишем и коммитим сразу.
        results: dict[str, _Result] = {}
        llm_jobs: list[tuple[str, str, Factors]] = []  # (reestr, card_text, factors)
        for t in tenders:
            f = compute_factors(t, blacklist_inns)
            hard = hard_exclusion(f)
            if hard is not None:
                r = _Result(t.reestr_number, 0, "noise", hard, "rules", f)
            elif is_auction(f):
                r = _Result(
                    t.reestr_number, 0, "auction", "электронный аукцион — отложено", "rules", f
                )
            else:
                llm_jobs.append((t.reestr_number, build_card_block(t), f))
                continue
            results[r.reestr_number] = r
            _write(r)
        session.commit()

        total_llm = len(llm_jobs)
        if progress_cb is not None:
            progress_cb(0, total_llm)

        # Фаза 2: арбитр создаётся лениво и только если есть что оценивать LLM.
        arb = arbiter
        if llm_jobs and arb is None:
            try:
                arb = create_arbiter()
            except ValueError as exc:
                log.warning("arbiter_unavailable", pending=len(llm_jobs), error=str(exc))
                arb = None

        if llm_jobs and arb is not None:
            scorer = arb  # для замыкания (mypy: не None)

            def _score_chunk(chunk: list[tuple[str, str, Factors]]) -> list[_Result]:
                verdicts = scorer.decide_batch([(reestr, card) for reestr, card, _ in chunk])
                return [
                    _Result(
                        reestr,
                        verdicts[reestr].score,
                        verdict_from_score(verdicts[reestr].score),
                        verdicts[reestr].summary,
                        scorer.provider,
                        factors,
                        reasoning=verdicts[reestr].reasoning or None,
                        confidence=verdicts[reestr].confidence,
                        red_flags=verdicts[reestr].red_flags or None,
                    )
                    for reestr, _, factors in chunk
                ]

            chunks = [llm_jobs[i : i + CHUNK_SIZE] for i in range(0, len(llm_jobs), CHUNK_SIZE)]
            workers = min(CHUNK_CONCURRENCY, len(chunks))
            done = 0
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for chunk, chunk_results in zip(
                    chunks, pool.map(_score_chunk, chunks), strict=True
                ):
                    for r in chunk_results:
                        results[r.reestr_number] = r
                        _write(r)
                    session.commit()  # фиксируем батч сразу — устойчиво к рестарту
                    done += len(chunk)
                    if progress_cb is not None:
                        progress_cb(done, total_llm)

            # Ретрай карточек, которые модель пропустила в батче (одним запросом).
            missed = [job for job in llm_jobs if results[job[0]].summary == NOT_SCORED]
            if missed:
                log.info("score_retry", missed=len(missed))
                for r in _score_chunk(missed):
                    if r.summary != NOT_SCORED:
                        results[r.reestr_number] = r
                        _write(r)
                session.commit()
        elif llm_jobs:
            skipped = len(llm_jobs)  # арбитр недоступен — оставляем в очереди

        counts = {"relevant": 0, "maybe": 0, "noise": 0, "auction": 0}
        for r in results.values():
            counts[r.verdict] += 1
        total = len(results)

    log.info(
        "score_done",
        total=total,
        relevant=counts["relevant"],
        maybe=counts["maybe"],
        noise=counts["noise"],
        auction=counts["auction"],
        sent_to_llm=len(llm_jobs) - skipped,
        skipped=skipped,
    )
    return ScoreSummary(
        total=total,
        relevant=counts["relevant"],
        maybe=counts["maybe"],
        noise=counts["noise"],
        auction=counts["auction"],
        sent_to_llm=len(llm_jobs) - skipped,
        skipped=skipped,
    )
