"""FastAPI-приложение веб-интерфейса (Фаза 2).

Запуск: uvicorn tender_ingest.web.app:app --host 0.0.0.0 --port 8000
Сессия — подписанная cookie (SessionMiddleware). Все страницы, кроме /login и
/health, требуют входа по общему паролю.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.middleware.sessions import SessionMiddleware

from tender_ingest.config import get_settings
from tender_ingest.logging import configure_logging
from tender_ingest.web.routes import (
    analytics,
    auth,
    blacklist,
    closed,
    documents,
    economics,
    recommend,
    score,
    tenders,
    tracking,
    upload,
)
from tender_ingest.web.security import NotAuthenticatedError

log = structlog.get_logger()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.web_password == "craft" or settings.session_secret == "dev-insecure-change-me":
        log.warning("insecure_defaults", detail="смените WEB_PASSWORD и SESSION_SECRET в .env")

    app = FastAPI(title="Закупки бюро", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="craft_session",
        same_site="lax",
        https_only=settings.session_https_only,  # в проде true — cookie только по HTTPS
        max_age=14 * 24 * 3600,
    )

    @app.exception_handler(NotAuthenticatedError)
    async def _redirect_to_login(request: Request, exc: NotAuthenticatedError) -> Response:
        return RedirectResponse("/login", status_code=303)

    @app.get("/health")
    def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app.include_router(auth.router)
    app.include_router(upload.router)
    app.include_router(score.router)
    app.include_router(documents.router)
    app.include_router(economics.router)
    app.include_router(closed.router)
    app.include_router(tracking.router)
    app.include_router(recommend.router)
    app.include_router(analytics.router)
    app.include_router(blacklist.router)
    app.include_router(tenders.router)
    return app


app = create_app()
