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


@main.command("economics-import")
@click.option(
    "--file",
    "file_",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Путь к .xlsx таблице «Экономика» (база знаний расчётов бюро)",
)
def economics_import_cmd(file_: Path) -> None:
    """Импортировать таблицу «Экономика»: блоки проектов -> база знаний долей по разделам.

    Повторный импорт полностью заменяет базу (снимок одного файла).
    """
    from tender_ingest.db.session import get_session_factory
    from tender_ingest.economics.store import EconomicsStore
    from tender_ingest.economics.xlsx import parse_workbook

    projects = parse_workbook(file_)
    with get_session_factory()() as session:
        summary = EconomicsStore(session).replace_import(projects, file_.name)
    click.echo(
        f"projects={summary.projects} lines={summary.lines} "
        f"without_canon={summary.lines_without_canon}"
    )


@main.command("labor-template")
@click.option(
    "--out",
    "out_",
    default=Path("Трудозатраты_шаблон.xlsx"),
    type=click.Path(dir_okay=False, path_type=Path),
    help="Куда сохранить Excel-шаблон для заполнения бюро",
)
def labor_template_cmd(out_: Path) -> None:
    """Сгенерировать Excel-шаблон трудозатрат (ставки ролей + часы по проектам)."""
    from tender_ingest.economics.labor import write_template

    write_template(out_)
    click.echo(f"Шаблон сохранён: {out_}")


@main.command("labor-import")
@click.option(
    "--file",
    "file_",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Заполненный шаблон трудозатрат (.xlsx)",
)
def labor_import_cmd(file_: Path) -> None:
    """Импортировать ставки ролей и факт часов (полная замена таблиц)."""
    from tender_ingest.db.session import get_session_factory
    from tender_ingest.economics.labor import import_workbook

    with get_session_factory()() as session:
        summary = import_workbook(file_, session)
    click.echo(
        f"rates={summary.rates} hours_rows={summary.hours_rows} "
        f"without_canon={summary.hours_without_canon}"
    )


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
