"""Хранилище базы знаний «Экономики» и расчётов экономики тендеров.

Импорт workbook полностью заменяет базу знаний (снимок одного файла). Для расчёта
наружу отдаются компактные слепки проектов: суммарная доля по каждому каноническому
разделу в рамках проекта (несколько строк одного раздела складываются).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from tender_ingest.db.models import EconomicsLine, EconomicsProject, Tender, TenderEconomics
from tender_ingest.economics.xlsx import ParsedProject


@dataclass(frozen=True)
class AnalogProject:
    """Слепок проекта для подбора аналогов и статистики долей/сумм."""

    id: int
    sheet: str  # work | preliminary
    title: str
    contract_total: float | None
    sections: dict[str, float]  # canon -> суммарная доля от цены договора (0..1)
    section_names: dict[str, list[str]]  # canon -> исходные названия строк
    amounts: dict[str, float] = field(default_factory=dict)  # canon -> сумма затрат, ₽


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
                EconomicsLine.planned,
                EconomicsLine.fact,
            ).where(EconomicsLine.canon.is_not(None))
        ).all()
        by_project: dict[int, dict[str, float]] = {}
        amounts: dict[int, dict[str, float]] = {}
        names: dict[int, dict[str, list[str]]] = {}
        for project_id, canon, share, name_raw, planned, fact in lines:
            if share is not None:
                sections = by_project.setdefault(project_id, {})
                sections[canon] = sections.get(canon, 0.0) + float(share)
            value = planned if planned is not None else fact
            if value is not None and value > 0:
                sums = amounts.setdefault(project_id, {})
                sums[canon] = sums.get(canon, 0.0) + float(value)
            names.setdefault(project_id, {}).setdefault(canon, []).append(name_raw)
        return [
            AnalogProject(
                id=p.id,
                sheet=p.sheet,
                title=p.title,
                contract_total=float(p.contract_total) if p.contract_total is not None else None,
                sections=by_project.get(p.id, {}),
                section_names=names.get(p.id, {}),
                amounts=amounts.get(p.id, {}),
            )
            for p in projects
        ]

    def bureau_margin_median_pct(self) -> float | None:
        """Медианная маржа бюро по базе: 1 − себестоимость/договор (в процентах)."""
        rows = self.session.execute(
            select(EconomicsProject.contract_total, EconomicsProject.cost_planned).where(
                EconomicsProject.contract_total.is_not(None),
                EconomicsProject.cost_planned.is_not(None),
            )
        ).all()
        margins = [
            float(1 - cost / contract) * 100
            for contract, cost in rows
            if contract and cost and 0 < cost < contract
        ]
        return round(statistics.median(margins), 1) if len(margins) >= 5 else None

    def knowledge_base_size(self) -> int:
        return len(self.session.execute(select(EconomicsProject.id)).all())

    def plan_fact_ratios(self) -> dict[str, tuple[float, int]]:
        """Медианные коэффициенты факт/план по канонам (сколько РЕАЛЬНО вышло против плана).

        Ключи: канон (n>=3), '__group_<группа>' (n>=5), '__all__' (n>=10). Строки без
        канона участвуют только в групповом/общем. Выбросы (x5 в любую сторону) отброшены.
        """
        from tender_ingest.economics.canon import CATALOG_BY_KEY

        rows = self.session.execute(
            select(EconomicsLine.canon, EconomicsLine.planned, EconomicsLine.fact).where(
                EconomicsLine.fact.is_not(None), EconomicsLine.planned.is_not(None)
            )
        ).all()
        by_canon: dict[str, list[float]] = {}
        by_group: dict[str, list[float]] = {}
        all_ratios: list[float] = []
        for canon, planned, fact in rows:
            p, f = float(planned), float(fact)
            if p <= 0 or f <= 0:
                continue
            ratio = f / p
            if not 0.2 <= ratio <= 5.0:
                continue
            all_ratios.append(ratio)
            if canon:
                by_canon.setdefault(canon, []).append(ratio)
                section = CATALOG_BY_KEY.get(canon)
                if section is not None:
                    by_group.setdefault(section.group, []).append(ratio)

        result: dict[str, tuple[float, int]] = {}
        for canon, ratios in by_canon.items():
            if len(ratios) >= 3:
                result[canon] = (round(statistics.median(ratios), 3), len(ratios))
        for group, ratios in by_group.items():
            if len(ratios) >= 5:
                result[f"__group_{group}"] = (round(statistics.median(ratios), 3), len(ratios))
        if len(all_ratios) >= 10:
            result["__all__"] = (round(statistics.median(all_ratios), 3), len(all_ratios))
        return result

    def market_drop_stats(self, law: str | None = None) -> dict[str, object] | None:
        """Снижение цены победителями на завершённых торгах из базы: 1 − победитель/НМЦК.

        Считается по тендерам с заполненным result.winner_offer (появляются из выгрузок
        Контура по завершённым закупкам). law сужает выборку; при <5 наблюдений с законом
        — фолбэк на все. None — данных нет вовсе.
        """
        rows = self.session.execute(
            select(Tender.nmck, Tender.result, Tender.law).where(Tender.nmck.is_not(None))
        ).all()

        def drops(law_filter: str | None) -> list[float]:
            out: list[float] = []
            for nmck, result, row_law in rows:
                if law_filter is not None and row_law != law_filter:
                    continue
                raw = (result or {}).get("winner_offer")
                if raw in (None, ""):
                    continue
                try:
                    winner = float(str(raw).replace("\xa0", "").replace(" ", "").replace(",", "."))
                except ValueError:
                    continue
                nmck_f = float(nmck)
                if winner <= 0 or nmck_f <= 0 or winner > nmck_f:
                    continue
                drop = 1.0 - winner / nmck_f
                if drop <= 0.7:  # снижение >70% — аномалия/ошибка данных
                    out.append(drop)
            return out

        values = drops(law)
        used_law = law
        if len(values) < 5:
            values = drops(None)
            used_law = None
        if len(values) < 5:
            return None
        values.sort()
        n = len(values)
        return {
            "median_pct": round(statistics.median(values) * 100, 1),
            "p25_pct": round(values[n // 4] * 100, 1),
            "p75_pct": round(values[(3 * n) // 4] * 100, 1),
            "n": n,
            "law": used_law,
        }

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

    def list_versions(self, reestr_number: str) -> list[TenderEconomics]:
        """Все версии расчёта по тендеру, новые сверху (для истории в редакторе)."""
        return list(
            self.session.execute(
                select(TenderEconomics)
                .where(TenderEconomics.reestr_number == reestr_number)
                .order_by(TenderEconomics.created_at.desc(), TenderEconomics.id.desc())
            )
            .scalars()
            .all()
        )


def contract_scale_note(contract_total: Decimal | float | None) -> str:
    """Человекочитаемый масштаб цены для промпта («8.0 млн ₽»)."""
    if contract_total is None:
        return "не указана"
    value = float(contract_total)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f} млн ₽"
    return f"{value / 1_000:.0f} тыс ₽"
