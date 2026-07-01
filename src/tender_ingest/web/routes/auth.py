"""Логин/логаут по общему паролю + простой rate-limit по IP."""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from tender_ingest.config import get_settings
from tender_ingest.web.security import check_password
from tender_ingest.web.templating import templates

router = APIRouter()

_MAX_FAILS = 10  # неудачных попыток за окно
_WINDOW = 300.0  # секунд
_fails: dict[str, list[float]] = {}  # ip -> времена неудачных попыток


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _too_many(ip: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _fails.get(ip, []) if now - t < _WINDOW]
    _fails[ip] = recent
    return len(recent) >= _MAX_FAILS


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> HTMLResponse:
    if request.session.get("auth"):
        return RedirectResponse("/", status_code=303)  # type: ignore[return-value]
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
def login(request: Request, password: Annotated[str, Form()]) -> HTMLResponse:
    ip = _client_ip(request)
    if _too_many(ip):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Слишком много попыток, подождите"}, status_code=429
        )
    if check_password(password, get_settings().web_password):
        _fails.pop(ip, None)
        request.session["auth"] = True
        return RedirectResponse("/", status_code=303)  # type: ignore[return-value]
    _fails.setdefault(ip, []).append(time.monotonic())
    return templates.TemplateResponse(
        request, "login.html", {"error": "Неверный пароль"}, status_code=401
    )


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
