"""CLI ручного запуска приёма (CLAUDE.md, раздел 7, задача 8)."""

from __future__ import annotations

from pathlib import Path

import click

from tender_ingest.config import get_settings
from tender_ingest.logging import configure_logging
from tender_ingest.pipeline import ingest_excel
from tender_ingest.relevance.scorer import score_pending


@click.group()
def main() -> None:
    """Tender ingest CLI (v1: выгрузка Контур.Закупок в Excel)."""
    configure_logging(get_settings().log_level)


@main.command("ingest")
@click.option(
    "--file",
    "file_",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Путь к .xlsx выгрузке Контура",
)
def ingest_cmd(file_: Path) -> None:
    """Загрузить одну выгрузку: парс -> нормализация -> upsert -> очередь анализа."""
    summary = ingest_excel(file_)
    click.echo(
        f"source={summary.source} file={summary.file} "
        f"rows={summary.rows_total} upserted={summary.tenders_upserted} "
        f"failures={summary.parse_failures} status={summary.status}"
    )
    if summary.status != "success":
        raise click.ClickException("Прогон завершился с ошибкой — см. журнал ingestion_runs")


@main.command("score")
@click.option("--limit", type=int, default=None, help="Сколько закупок из очереди оценить")
def score_cmd(limit: int | None) -> None:
    """Оценить закупки из очереди: факторы -> Claude (score + резюме)."""
    summary = score_pending(limit=limit)
    click.echo(
        f"total={summary.total} relevant={summary.relevant} maybe={summary.maybe} "
        f"auction={summary.auction} noise={summary.noise} "
        f"claude={summary.sent_to_llm} skipped={summary.skipped}"
    )


if __name__ == "__main__":
    main()
