"""Экономика тендера: запуск расчёта ИИ, правки строк, рекомендации ревью, экспорт."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from tender_ingest.db.session import get_session_factory
from tender_ingest.economics.engine import apply_edits
from tender_ingest.economics.export import build_economics_xlsx
from tender_ingest.economics.store import EconomicsStore
from tender_ingest.web.economics_job import job as eco_job
from tender_ingest.web.repository import WebRepository
from tender_ingest.web.security import require_auth

router = APIRouter(dependencies=[Depends(require_auth)])


def _detail(reestr_number: str, msg: str | None = None) -> RedirectResponse:
    target = f"/tender/{reestr_number}"
    if msg:
        target += f"?{urlencode({'msg': msg})}"
    return RedirectResponse(target + "#economics", status_code=303)


def _to_float(raw: object) -> float | None:
    text = str(raw or "").replace("\xa0", "").replace(" ", "").replace(",", ".").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


@router.post("/tender/{reestr_number}/economics/propose")
def propose_economics(request: Request, reestr_number: str, deep: str = "") -> RedirectResponse:
    """Запустить расчёт экономики в фоне: бриф ТЗ + аналоги -> Claude -> движок."""
    with get_session_factory()() as session:
        if WebRepository(session).get(reestr_number) is None:
            return _detail(reestr_number, "Тендер не найден")
    started = eco_job.start(reestr_number, deep=bool(deep.strip()))
    msg = (
        "Расчёт экономики запущен в фоне — займёт около минуты"
        if started
        else "Уже идёт другой расчёт экономики — дождитесь его завершения"
    )
    return _detail(reestr_number, msg)


@router.get("/tender/{reestr_number}/economics/status")
def economics_status(request: Request, reestr_number: str) -> JSONResponse:
    """Идёт ли расчёт ИМЕННО этого тендера (для поллинга с карточки)."""
    snapshot = eco_job.snapshot()
    return JSONResponse({"running": snapshot.running and snapshot.reestr_number == reestr_number})


def _changed(current: object, new: float | None) -> bool:
    if new is None:
        return False
    if current is None:
        return True
    return abs(float(str(current)) - new) > 0.004


@router.post("/tender/{reestr_number}/economics/{calc_id}/edit")
async def edit_economics(request: Request, reestr_number: str, calc_id: int) -> RedirectResponse:
    """Правки человека: изменённые суммы/доли -> пересчёт итогов -> новая версия."""
    form = await request.form()
    with get_session_factory()() as session:
        store = EconomicsStore(session)
        calc = store.get(reestr_number, calc_id)
        if calc is None:
            return _detail(reestr_number, "Расчёт не найден")
        payload: dict[str, Any] = dict(calc.payload)

        edits: dict[str, dict[str, float | None]] = {}
        buckets = {"l": list(payload.get("lines", [])), "o": list(payload.get("overheads", []))}
        for prefix, lines in buckets.items():
            for i, line in enumerate(lines):
                amount = _to_float(form.get(f"amount_{prefix}{i}"))
                share = _to_float(form.get(f"share_{prefix}{i}"))
                if _changed(line.get("amount"), amount):
                    edits[f"{prefix}{i}"] = {"amount": amount}
                elif _changed(line.get("share_pct"), share):
                    edits[f"{prefix}{i}"] = {"share_pct": share}

        margin = _to_float(form.get("min_margin_pct"))
        current_margin = float(payload.get("params", {}).get("min_margin_pct", 0.0))
        margin_changed = margin is not None and abs(margin - current_margin) > 0.004
        if not edits and not margin_changed:
            return _detail(reestr_number, "Изменений нет — расчёт не пересохранён")

        new_payload = apply_edits(payload, edits, min_margin_pct=margin if margin_changed else None)
        store.add_calculation(reestr_number, created_by="user", model=None, payload=new_payload)
    return _detail(reestr_number, "Правки сохранены, итоги пересчитаны (новая версия)")


@router.post("/tender/{reestr_number}/economics/{calc_id}/apply-review")
def apply_review(request: Request, reestr_number: str, calc_id: int) -> RedirectResponse:
    """Применить рекомендации ИИ-ревью с конкретными суммами (новая версия расчёта)."""
    with get_session_factory()() as session:
        store = EconomicsStore(session)
        calc = store.get(reestr_number, calc_id)
        if calc is None:
            return _detail(reestr_number, "Расчёт не найден")
        payload: dict[str, Any] = dict(calc.payload)
        review = payload.get("review")
        if not isinstance(review, dict):
            return _detail(reestr_number, "У расчёта нет ИИ-ревью")

        edits: dict[str, dict[str, float | None]] = {}
        applied_keys: list[str] = []
        for adj in review.get("adjustments", []):
            key = str(adj.get("key", ""))
            amount = adj.get("suggested_amount")
            if amount is None or not re.fullmatch(r"[lo]\d+", key):
                continue
            edits[key] = {"amount": float(amount)}
            applied_keys.append(key)
        if not edits:
            return _detail(reestr_number, "В ревью нет рекомендаций с конкретными суммами")

        new_payload = apply_edits(payload, edits)
        for key in applied_keys:
            bucket = new_payload["lines"] if key.startswith("l") else new_payload["overheads"]
            line = bucket[int(key[1:])]
            line["source"] = "ai"
            line["note"] = (line.get("note") or "") + " · применена рекомендация ИИ-ревью"
        new_payload["review"] = {**review, "applied": True}
        store.add_calculation(reestr_number, created_by="ai", model=calc.model, payload=new_payload)
    return _detail(
        reestr_number, f"Применено рекомендаций: {len(applied_keys)} — итоги пересчитаны"
    )


@router.get("/tender/{reestr_number}/economics/{calc_id}/export.xlsx")
def export_economics(request: Request, reestr_number: str, calc_id: int) -> Response:
    """Скачать расчёт в .xlsx в формате таблицы «Экономика»."""
    with get_session_factory()() as session:
        store = EconomicsStore(session)
        calc = store.get(reestr_number, calc_id)
        if calc is None:
            return Response(status_code=404)
        found = WebRepository(session).get(reestr_number)
        title = (found[0].subject or reestr_number) if found else reestr_number
        data = build_economics_xlsx(dict(calc.payload), title=title, reestr=reestr_number)
    name = quote(f"Экономика_{reestr_number}.xlsx")
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{name}"},
    )
