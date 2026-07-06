"""Хранилище базы знаний «Экономики» и расчётов экономики тендеров.

Импорт workbook полностью заменяет базу знаний (снимок одного файла). Для расчёта
наружу отдаются компактные слепки проектов: суммарная доля по каждому каноническому
разделу в рамках проекта (несколько строк одного раздела складываются).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from tender_ingest.db.models import EconomicsLine, EconomicsProject, TenderEconomics
from tender_ingest.economics.xlsx import ParsedProject


@dataclass(frozen=True)
class AnalogProject:
    """Слепок проекта для подбора аналогов и статистики долей."""

    id: int
    sheet: str  # work | preliminary
    title: str
    contract_total: float | None
    sections: dict[str, float]  # canon -> суммарная доля от цены договора (0..1)
    section_names: dict[str, list[str]]  # canon -> исходные названия строк


@dataclass(frozen=True)
class ImportSummary:
    projects: int
    lines: int
    lines_without_canon: int


class EconomicsStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def replace_import(self, projects: list[ParsedProject], source_file: str) -> ImportSummary:
        """Полная замена базы знаний содержимым разобранного workbook."""
        self.session.execute(delete(EconomicsProject))
        lines_total = 0
        no_canon = 0
        for parsed in projects:
            row = EconomicsProject(
                sheet=parsed.sheet,
                position=parsed.position,
                title=parsed.title,
                contract_total=parsed.contract_total,
                contract_note=parsed.contract_note,
                cost_planned=parsed.cost_planned,
                cost_fact=parsed.cost_fact,
                profit=parsed.profit,
                meta=dict(parsed.meta),
                source_file=source_file,
            )
            self.session.add(row)
            self.session.flush()  # нужен row.id для строк
            for line in parsed.lines:
                self.session.add(
                    EconomicsLine(
                        project_id=row.id,
                        position=line.position,
                        name_raw=line.name_raw,
                        canon=line.canon,
                        pct=line.pct,
                        planned=line.planned,
                        fact=line.fact,
                        fact_raw=line.fact_raw,
                        comment=line.comment,
                        share=line.share,
                    )
                )
                lines_total += 1
                if line.canon is None:
                    no_canon += 1
        self.session.commit()
        return ImportSummary(
            projects=len(projects), lines=lines_total, lines_without_canon=no_canon
        )

    def analog_projects(self) -> list[AnalogProject]:
        """Все проекты базы знаний со сведёнными долями по каноническим разделам."""
        projects = (
            self.session.execute(
                select(EconomicsProject).order_by(EconomicsProject.sheet, EconomicsProject.position)
            )
            .scalars()
            .all()
        )
        lines = self.session.execute(
            select(
                EconomicsLine.project_id,
                EconomicsLine.canon,
                EconomicsLine.share,
                EconomicsLine.name_raw,
            ).where(EconomicsLine.canon.is_not(None), EconomicsLine.share.is_not(None))
        ).all()
        by_project: dict[int, dict[str, float]] = {}
        names: dict[int, dict[str, list[str]]] = {}
        for project_id, canon, share, name_raw in lines:
            sections = by_project.setdefault(project_id, {})
            sections[canon] = sections.get(canon, 0.0) + float(share)
            names.setdefault(project_id, {}).setdefault(canon, []).append(name_raw)
        return [
            AnalogProject(
                id=p.id,
                sheet=p.sheet,
                title=p.title,
                contract_total=float(p.contract_total) if p.contract_total is not None else None,
                sections=by_project.get(p.id, {}),
                section_names=names.get(p.id, {}),
            )
            for p in projects
        ]

    def knowledge_base_size(self) -> int:
        return len(self.session.execute(select(EconomicsProject.id)).all())

    # --- расчёты экономики тендера (append-only) ---

    def add_calculation(
        self,
        reestr_number: str,
        *,
        created_by: str,
        model: str | None,
        payload: dict[str, object],
    ) -> TenderEconomics:
        row = TenderEconomics(
            reestr_number=reestr_number, created_by=created_by, model=model, payload=payload
        )
        self.session.add(row)
        self.session.commit()
        return row

    def latest_for(self, reestr_number: str) -> TenderEconomics | None:
        return self.session.execute(
            select(TenderEconomics)
            .where(TenderEconomics.reestr_number == reestr_number)
            .order_by(TenderEconomics.created_at.desc(), TenderEconomics.id.desc())
            .limit(1)
        ).scalar_one_or_none()

    def get(self, reestr_number: str, calc_id: int) -> TenderEconomics | None:
        """Расчёт с проверкой принадлежности тендеру (IDOR-safe)."""
        return self.session.execute(
            select(TenderEconomics).where(
                TenderEconomics.id == calc_id, TenderEconomics.reestr_number == reestr_number
            )
        ).scalar_one_or_none()


def contract_scale_note(contract_total: Decimal | float | None) -> str:
    """Человекочитаемый масштаб цены для промпта («8.0 млн ₽»)."""
    if contract_total is None:
        return "не указана"
    value = float(contract_total)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f} млн ₽"
    return f"{value / 1_000:.0f} тыс ₽"
