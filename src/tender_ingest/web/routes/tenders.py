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
def index(
    request: Request,
    verdict: str | None = None,
    law: str | None = None,
    region_code: str | None = None,
    search: str | None = None,
    sort: str = "score",
    page: int = 1,
) -> HTMLResponse:
    f = Filters(
        verdict=verdict, law=law, region_code=region_code, search=search, sort=sort, page=page
    ).normalized()
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
