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
    tenders_new: int = 0  # впервые увиденные в этой выгрузке
    tenders_existing: int = 0  # уже были в базе (в нейронку повторно не идут)


def ingest(source: SourceAdapter, file: str) -> IngestSummary:
    """Прогнать один источник: upsert каждой закупки, журнал, членство в выгрузке, очередь."""
    factory = get_session_factory()
    rows_total = 0
    upserted = 0
    failures = 0
    new_count = 0

    with factory() as session:
        runs = RunRepository(session)
        repo = TenderRepository(session)
        run_id = runs.start(source=source.name, file=file)
        session.commit()

        # Номера, уже бывшие в базе ДО этой выгрузки — чтобы отличить новые от известных.
        existing = repo.existing_reestr_numbers()
        seen: set[str] = set()

        status = "success"
        error: str | None = None
        try:
            for tender in source.fetch():
                rows_total += 1
                try:
                    # SAVEPOINT на строку: плохая строка откатывается одна, не весь файл.
                    with session.begin_nested():
                        repo.upsert(tender)
                        repo.add_upload_membership(run_id, tender.reestr_number)
                    upserted += 1
                    if tender.reestr_number not in seen:
                        seen.add(tender.reestr_number)
                        if tender.reestr_number not in existing:
                            new_count += 1
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
            existing_count = len(seen) - new_count
            runs.finish(
                run_id,
                rows_total=rows_total,
                tenders_upserted=upserted,
                parse_failures=failures,
                status=status,
                error=error,
                tenders_new=new_count,
                tenders_existing=existing_count,
            )
            session.commit()

    log.info(
        "ingest_done",
        source=source.name,
        file=file,
        rows_total=rows_total,
        tenders_upserted=upserted,
        parse_failures=failures,
        tenders_new=new_count,
        status=status,
    )
    return IngestSummary(
        source=source.name,
        file=file,
        rows_total=rows_total,
        tenders_upserted=upserted,
        parse_failures=failures,
        status=status,
        tenders_new=new_count,
        tenders_existing=len(seen) - new_count,
    )


def ingest_excel(path: str | Path, file_label: str | None = None) -> IngestSummary:
    """Удобная обёртка v1: загрузить выгрузку Контура из .xlsx.

    file_label — имя выгрузки для журнала (веб передаёт исходное имя файла, а не путь
    к временному файлу). По умолчанию — сам путь.
    """
    p = Path(path)
    return ingest(ExcelSource(p), file=file_label or str(p))
