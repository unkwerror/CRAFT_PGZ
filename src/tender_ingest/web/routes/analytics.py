"""Страница аналитики: рыночные метрики + метрики участия, по когортам (фаза B)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from tender_ingest.db.session import get_session_factory
from tender_ingest.web.analytics import COHORT_LABELS, COHORTS, AnalyticsRepository
from tender_ingest.web.security import require_auth
from tender_ingest.web.templating import templates
from tender_ingest.web.tracking import STATUS_LABELS

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/analytics", response_class=HTMLResponse)
def analytics_page(request: Request, cohort: str | None = None) -> HTMLResponse:
    selected = cohort if cohort in COHORTS else "all"
    with get_session_factory()() as session:
        repo = AnalyticsRepository(session)
        market = repo.market(selected)
        monthly = repo.monthly(selected)
        customers = repo.top_customers(selected)
        participation = repo.participation()
    max_month_count = max((p.count for p in monthly), default=0)
    return templates.TemplateResponse(
        request,
        "analytics.html",
        {
            "cohort": selected,
            "cohorts": COHORTS,
            "cohort_labels": COHORT_LABELS,
            "market": market,
            "monthly": monthly,
            "max_month_count": max_month_count,
            "customers": customers,
            "p": participation,
            "status_labels": STATUS_LABELS,
        },
    )
