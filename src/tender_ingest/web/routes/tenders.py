"""Список закупок с фильтрами/сортировкой и карточка закупки."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from tender_ingest.db.session import get_session_factory
from tender_ingest.documents.prompt import FIELD_LABELS
from tender_ingest.economics.store import EconomicsStore
from tender_ingest.web.doc_analysis_job import job as doc_analysis_job
from tender_ingest.web.economics_job import job as economics_job
from tender_ingest.web.repository import PAGE_SIZE, DocumentRepository, Filters, WebRepository
from tender_ingest.web.scoring_job import job
from tender_ingest.web.security import require_auth
from tender_ingest.web.templating import templates
from tender_ingest.web.tracking import STATUS_LABELS, TrackingRepository

_FINISHED_BANNER_SEC = 120  # сколько секунд показывать итог завершённого прогона

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/", response_class=HTMLResponse)
def index(  # noqa: PLR0913 — плоский разбор query-параметров формы фильтров
    request: Request,
    search: str | None = None,
    exact: str | None = None,
    exclude: str | None = None,
    verdict: str | None = None,
    region_code: str | None = None,
    law: str | None = None,
    nmck_min: str | None = None,
    nmck_max: str | None = None,
    upload: str | None = None,
    fav: str | None = None,
    sort: str | None = None,
    page: str | None = None,
    msg: str | None = None,
) -> HTMLResponse:
    f = Filters.from_query(
        search=search,
        exact=exact,
        exclude=exclude,
        verdict=verdict,
        region_code=region_code,
        law=law,
        nmck_min=nmck_min,
        nmck_max=nmck_max,
        upload=upload,
        fav=fav,
        sort=sort,
        page=page,
    )
    with get_session_factory()() as session:
        repo = WebRepository(session)
        rows, total = repo.list_tenders(f)
        facets = repo.facets()
        pending = repo.pending_count()
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    scoring = job.snapshot()
    scoring_msg = None
    if not scoring.running and scoring.message and scoring.finished_at is not None:
        age = (dt.datetime.now(dt.UTC) - scoring.finished_at).total_seconds()
        if age < _FINISHED_BANNER_SEC:
            scoring_msg = scoring.message
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
            "scoring": scoring,
            "scoring_msg": scoring_msg,
        },
    )


@router.get("/tender/{reestr_number}", response_class=HTMLResponse)
def detail(request: Request, reestr_number: str, msg: str | None = None) -> HTMLResponse:
    with get_session_factory()() as session:
        found = WebRepository(session).get(reestr_number)
        if found is None:
            return templates.TemplateResponse(
                request, "not_found.html", {"reestr_number": reestr_number}, status_code=404
            )
        tender, rel = found
        docs_repo = DocumentRepository(session)
        documents = docs_repo.list_for(reestr_number)
        analyses = docs_repo.latest_analyses_for(reestr_number)

        tracking = TrackingRepository(session)
        is_favorite = tracking.is_favorite(reestr_number)
        participation = tracking.get_participation(reestr_number)
        notes = tracking.list_notes(reestr_number)

        eco_store = EconomicsStore(session)
        economics = eco_store.latest_for(reestr_number)
        kb_size = eco_store.knowledge_base_size()

        dj = doc_analysis_job.snapshot()
        doc_job_msg = None
        if (
            not dj.running
            and dj.message
            and dj.finished_at is not None
            and (dt.datetime.now(dt.UTC) - dj.finished_at).total_seconds() < _FINISHED_BANNER_SEC
        ):
            doc_job_msg = dj.message

        ej = economics_job.snapshot()
        eco_job_msg = None
        if (
            not ej.running
            and ej.message
            and ej.reestr_number == reestr_number
            and ej.finished_at is not None
            and (dt.datetime.now(dt.UTC) - ej.finished_at).total_seconds() < _FINISHED_BANNER_SEC
        ):
            eco_job_msg = ej.message
        return templates.TemplateResponse(
            request,
            "tenders/detail.html",
            {
                "t": tender,
                "rel": rel,
                "documents": documents,
                "analyses": analyses,
                "field_labels": FIELD_LABELS,
                "msg": msg,
                "doc_job": dj,
                "doc_job_msg": doc_job_msg,
                "economics": economics,
                "eco_kb_size": kb_size,
                "eco_job": ej,
                "eco_job_msg": eco_job_msg,
                "is_favorite": is_favorite,
                "participation": participation,
                "notes": notes,
                "status_labels": STATUS_LABELS,
            },
        )
