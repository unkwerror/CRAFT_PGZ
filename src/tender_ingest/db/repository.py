"""Репозиторий: идемпотентный upsert по `reestr_number`, очередь, журнал прогонов.

Повторная выгрузка не плодит дубли: ON CONFLICT (reestr_number) DO UPDATE.
Очередь анализа пополняется ON CONFLICT DO NOTHING — повторная загрузка не
сбрасывает статус уже взятых в работу закупок (CLAUDE.md, раздел 9).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tender_ingest.db.models import (
    AnalysisQueue,
    IngestionRun,
    Tender,
    TenderRaw,
    TenderRelevance,
)
from tender_ingest.sources.base import RawTender

# Колонки tenders, которые обновляем при конфликте (всё, кроме PK и created_at).
_TENDER_UPDATE_COLS = (
    "source",
    "subject",
    "nmck",
    "currency",
    "law",
    "purchase_method",
    "stage",
    "etp",
    "smp_sono",
    "publish_date",
    "submission_deadline",
    "delivery_place",
    "securities",
    "advance_raw",
    "advance_pct",
    "customer_name",
    "customer_inn",
    "customer_kpp",
    "region_code",
    "region_name",
    "result",
)


class TenderRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, tender: RawTender) -> None:
        """Идемпотентно сохранить закупку, сырьё и поставить в очередь анализа."""
        values = {
            "reestr_number": tender.reestr_number,
            "source": tender.source,
            "subject": tender.subject,
            "nmck": tender.nmck,
            "currency": tender.currency,
            "law": tender.law,
            "purchase_method": tender.purchase_method,
            "stage": tender.stage,
            "etp": tender.etp,
            "smp_sono": tender.smp_sono,
            "publish_date": tender.publish_date,
            "submission_deadline": tender.submission_deadline,
            "delivery_place": tender.delivery_place,
            "securities": {k: v.model_dump(mode="json") for k, v in tender.securities.items()},
            "advance_raw": tender.advance_raw,
            "advance_pct": tender.advance_pct,
            "customer_name": tender.customer_name,
            "customer_inn": tender.customer_inn,
            "customer_kpp": tender.customer_kpp,
            "region_code": tender.region_code,
            "region_name": tender.region_name,
            "result": tender.result.model_dump(mode="json"),
        }
        stmt = insert(Tender).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Tender.reestr_number],
            set_={c: stmt.excluded[c] for c in _TENDER_UPDATE_COLS} | {"updated_at": func.now()},
        )
        self.session.execute(stmt)

        raw_stmt = insert(TenderRaw).values(
            reestr_number=tender.reestr_number,
            source=tender.source,
            payload=tender.raw,
        )
        raw_stmt = raw_stmt.on_conflict_do_update(
            index_elements=[TenderRaw.reestr_number],
            set_={
                "source": raw_stmt.excluded.source,
                "payload": raw_stmt.excluded.payload,
                "fetched_at": func.now(),
            },
        )
        self.session.execute(raw_stmt)

        q_stmt = insert(AnalysisQueue).values(reestr_number=tender.reestr_number)
        q_stmt = q_stmt.on_conflict_do_nothing(index_elements=[AnalysisQueue.reestr_number])
        self.session.execute(q_stmt)


class RelevanceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def pending(self, limit: int | None = None) -> Sequence[Tender]:
        """Полные карточки закупок в очереди со статусом 'pending'."""
        stmt = (
            select(Tender)
            .join(AnalysisQueue, AnalysisQueue.reestr_number == Tender.reestr_number)
            .where(AnalysisQueue.status == "pending")
            .order_by(Tender.reestr_number)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return self.session.execute(stmt).scalars().all()

    def upsert(
        self,
        reestr_number: str,
        *,
        score: int,
        verdict: str,
        decided_by: str,
        summary: str | None,
        factors: dict[str, object],
    ) -> None:
        values = {
            "reestr_number": reestr_number,
            "score": score,
            "verdict": verdict,
            "decided_by": decided_by,
            "summary": summary,
            "factors": factors,
        }
        stmt = insert(TenderRelevance).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[TenderRelevance.reestr_number],
            set_={
                "score": stmt.excluded.score,
                "verdict": stmt.excluded.verdict,
                "decided_by": stmt.excluded.decided_by,
                "summary": stmt.excluded.summary,
                "factors": stmt.excluded.factors,
                "scored_at": func.now(),
            },
        )
        self.session.execute(stmt)
        self.session.execute(
            update(AnalysisQueue)
            .where(AnalysisQueue.reestr_number == reestr_number)
            .values(status="scored")
        )


class RunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def start(self, source: str, file: str) -> int:
        run = IngestionRun(source=source, file=file, status="running")
        self.session.add(run)
        self.session.flush()
        return run.id

    def finish(
        self,
        run_id: int,
        *,
        rows_total: int,
        tenders_upserted: int,
        parse_failures: int,
        status: str,
        error: str | None = None,
    ) -> None:
        self.session.execute(
            update(IngestionRun)
            .where(IngestionRun.id == run_id)
            .values(
                finished_at=dt.datetime.now(dt.UTC),
                rows_total=rows_total,
                tenders_upserted=tenders_upserted,
                parse_failures=parse_failures,
                status=status,
                error=error,
            )
        )
