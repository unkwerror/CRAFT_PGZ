"""Оркестрация: ExcelSource.fetch() -> normalize -> upsert -> enqueue (CLAUDE.md, раздел 7).

Pipeline работает только с `RawTender` (через `SourceAdapter`), не зная про Excel.
Один прогон = один файл = одна запись в журнале `ingestion_runs`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog

from tender_ingest.db.repository import RunRepository, TenderRepository
from tender_ingest.db.session import get_session_factory
from tender_ingest.sources.base import SourceAdapter
from tender_ingest.sources.excel_source import ExcelSource

log = structlog.get_logger()


@dataclass
class IngestSummary:
    source: str
    file: str
    rows_total: int
    tenders_upserted: int
    parse_failures: int
    status: str


def ingest(source: SourceAdapter, file: str) -> IngestSummary:
    """Прогнать один источник: upsert каждой закупки, журнал, очередь анализа."""
    factory = get_session_factory()
    rows_total = 0
    upserted = 0
    failures = 0

    with factory() as session:
        runs = RunRepository(session)
        repo = TenderRepository(session)
        run_id = runs.start(source=source.name, file=file)
        session.commit()

        status = "success"
        error: str | None = None
        try:
            for tender in source.fetch():
                rows_total += 1
                try:
                    # SAVEPOINT на строку: плохая строка откатывается одна, не весь файл.
                    with session.begin_nested():
                        repo.upsert(tender)
                    upserted += 1
                except Exception as exc:
                    failures += 1
                    log.warning(
                        "tender_upsert_failed",
                        reestr_number=tender.reestr_number,
                        error=str(exc),
                    )
            session.commit()
        except Exception as exc:
            status = "failed"
            error = str(exc)
            session.rollback()
            log.error("ingest_failed", file=file, error=error)
        finally:
            runs.finish(
                run_id,
                rows_total=rows_total,
                tenders_upserted=upserted,
                parse_failures=failures,
                status=status,
                error=error,
            )
            session.commit()

    log.info(
        "ingest_done",
        source=source.name,
        file=file,
        rows_total=rows_total,
        tenders_upserted=upserted,
        parse_failures=failures,
        status=status,
    )
    return IngestSummary(
        source=source.name,
        file=file,
        rows_total=rows_total,
        tenders_upserted=upserted,
        parse_failures=failures,
        status=status,
    )


def ingest_excel(path: str | Path) -> IngestSummary:
    """Удобная обёртка v1: загрузить выгрузку Контура из .xlsx."""
    p = Path(path)
    return ingest(ExcelSource(p), file=str(p))
