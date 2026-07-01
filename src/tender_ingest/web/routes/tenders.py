"""Список закупок с фильтрами/сортировкой и карточка закупки."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from tender_ingest.db.session import get_session_factory
from tender_ingest.web.repository import PAGE_SIZE, DocumentRepository, Filters, WebRepository
from tender_ingest.web.security import require_auth
from tender_ingest.web.templating import templates

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/", response_class=HTMLResponse)
def index(  # noqa: PLR0913 — плоский разбор query-параметров формы фильтров
    request: Request,
    search: str | None = None,
    exact: str | None = None,
    exclude: str | None = None,
    customer: str | None = None,
    delivery: str | None = None,
    laws: Annotated[list[str] | None, Query()] = None,
    stages: Annotated[list[str] | None, Query()] = None,
    verdict: str | None = None,
    region_code: str | None = None,
    purchase_method: str | None = None,
    etp: str | None = None,
    smp_sono: str | None = None,
    decided_by: str | None = None,
    currency: str | None = None,
    source: str | None = None,
    nmck_min: str | None = None,
    nmck_max: str | None = None,
    nmck_none: str | None = None,
    bid_min: str | None = None,
    bid_max: str | None = None,
    bid_none: str | None = None,
    contract_min: str | None = None,
    contract_max: str | None = None,
    contract_none: str | None = None,
    advance: str | None = None,
    score_min: str | None = None,
    score_max: str | None = None,
    publish_from: str | None = None,
    publish_to: str | None = None,
    deadline_from: str | None = None,
    deadline_to: str | None = None,
    sort: str | None = None,
    page: str | None = None,
    msg: str | None = None,
) -> HTMLResponse:
    f = Filters.from_query(
        search=search,
        exact=exact,
        exclude=exclude,
        customer=customer,
        delivery=delivery,
        laws=laws,
        stages=stages,
        verdict=verdict,
        region_code=region_code,
        purchase_method=purchase_method,
        etp=etp,
        smp_sono=smp_sono,
        decided_by=decided_by,
        currency=currency,
        source=source,
        nmck_min=nmck_min,
        nmck_max=nmck_max,
        nmck_none=nmck_none,
        bid_min=bid_min,
        bid_max=bid_max,
        bid_none=bid_none,
        contract_min=contract_min,
        contract_max=contract_max,
        contract_none=contract_none,
        advance=advance,
        score_min=score_min,
        score_max=score_max,
        publish_from=publish_from,
        publish_to=publish_to,
        deadline_from=deadline_from,
        deadline_to=deadline_to,
        sort=sort,
        page=page,
    )
    with get_session_factory()() as session:
        repo = WebRepository(session)
        rows, total = repo.list_tenders(f)
        facets = repo.facets()
        pending = repo.pending_count()
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
            "pending": pending,
            "msg": msg,
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
        documents = DocumentRepository(session).list_for(reestr_number)
        return templates.TemplateResponse(
            request, "tenders/detail.html", {"t": tender, "rel": rel, "documents": documents}
        )
