"""Список закупок с фильтрами/сортировкой и карточка закупки."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from tender_ingest.db.session import get_session_factory
from tender_ingest.web.repository import PAGE_SIZE, Filters, WebRepository
from tender_ingest.web.security import require_auth
from tender_ingest.web.templating import templates

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/", response_class=HTMLResponse)
def index(  # noqa: PLR0913 — плоский разбор query-параметров формы фильтров
    request: Request,
    search: str | None = None,
    customer: str | None = None,
    delivery: str | None = None,
    verdict: str | None = None,
    law: str | None = None,
    region_code: str | None = None,
    purchase_method: str | None = None,
    stage: str | None = None,
    etp: str | None = None,
    smp_sono: str | None = None,
    decided_by: str | None = None,
    currency: str | None = None,
    source: str | None = None,
    nmck_min: str | None = None,
    nmck_max: str | None = None,
    score_min: str | None = None,
    score_max: str | None = None,
    publish_from: str | None = None,
    publish_to: str | None = None,
    deadline_from: str | None = None,
    deadline_to: str | None = None,
    has_advance: str | None = None,
    sort: str | None = None,
    page: str | None = None,
) -> HTMLResponse:
    f = Filters.from_query(
        search=search,
        customer=customer,
        delivery=delivery,
        verdict=verdict,
        law=law,
        region_code=region_code,
        purchase_method=purchase_method,
        stage=stage,
        etp=etp,
        smp_sono=smp_sono,
        decided_by=decided_by,
        currency=currency,
        source=source,
        nmck_min=nmck_min,
        nmck_max=nmck_max,
        score_min=score_min,
        score_max=score_max,
        publish_from=publish_from,
        publish_to=publish_to,
        deadline_from=deadline_from,
        deadline_to=deadline_to,
        has_advance=has_advance,
        sort=sort,
        page=page,
    )
    with get_session_factory()() as session:
        repo = WebRepository(session)
        rows, total = repo.list_tenders(f)
        facets = repo.facets()
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    return templates.TemplateResponse(
        request,
        "tenders/list.html",
        {
            "rows": rows,
            "total": total,
            "facets": facets,
            "f": f,
            "page": f.page,
            "total_pages": total_pages,
        },
    )


@router.get("/tender/{reestr_number}", response_class=HTMLResponse)
def detail(request: Request, reestr_number: str) -> HTMLResponse:
    with get_session_factory()() as session:
        found = WebRepository(session).get(reestr_number)
        if found is None:
            return templates.TemplateResponse(
                request, "not_found.html", {"reestr_number": reestr_number}, status_code=404
            )
        tender, rel = found
        return templates.TemplateResponse(request, "tenders/detail.html", {"t": tender, "rel": rel})
