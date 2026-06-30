"""Логин/логаут по общему паролю."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from tender_ingest.config import get_settings
from tender_ingest.web.security import check_password
from tender_ingest.web.templating import templates

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> HTMLResponse:
    if request.session.get("auth"):
        return RedirectResponse("/", status_code=303)  # type: ignore[return-value]
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
def login(request: Request, password: Annotated[str, Form()]) -> HTMLResponse:
    if check_password(password, get_settings().web_password):
        request.session["auth"] = True
        return RedirectResponse("/", status_code=303)  # type: ignore[return-value]
    return templates.TemplateResponse(
        request, "login.html", {"error": "Неверный пароль"}, status_code=401
    )


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
