"""Экономика тендера: запуск расчёта ИИ, редактор с живым пересчётом, ревью, экспорт."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from tender_ingest.db.session import get_session_factory
from tender_ingest.economics.engine import (
    apply_editor_state,
    apply_edits,
    canon_median_hints,
    occupied_design_canons,
)
from tender_ingest.economics.export import build_economics_xlsx
from tender_ingest.economics.store import EconomicsStore
from tender_ingest.web.economics_job import job as eco_job
from tender_ingest.web.progress import time_progress
from tender_ingest.web.repository import DocumentRepository, WebRepository
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
    """Запустить расчёт экономики в фоне: бриф ТЗ + аналоги -> Claude -> движок.

    Предусловия проверяются ЗДЕСЬ, до старта джобы — пользователь сразу видит причину.
    НМЦК не обязательна: без неё формируется цена ПРЕДЛОЖЕНИЯ от себестоимости.
    """
    with get_session_factory()() as session:
        if WebRepository(session).get(reestr_number) is None:
            return _detail(reestr_number, "Тендер не найден")
        if not DocumentRepository(session).latest_analyses_for(reestr_number):
            return _detail(
                reestr_number,
                "Сначала разберите ТЗ (кнопка «Разобрать ТЗ» у документа) — "
                "расчёт экономики идёт по брифу",
            )
        if EconomicsStore(session).knowledge_base_size() == 0:
            return _detail(
                reestr_number,
                "База «Экономики» пуста — импортируйте файл: tender economics-import --file …",
            )
    started = eco_job.start(reestr_number, deep=bool(deep.strip()))
    msg = (
        "Расчёт экономики запущен в фоне — займёт 1–3 минуты"
        if started
        else "Уже идёт другой расчёт экономики — дождитесь его завершения"
    )
    return _detail(reestr_number, msg)


@router.post("/tender/{reestr_number}/nmck")
def set_nmck(request: Request, reestr_number: str, nmck: str = "") -> RedirectResponse:
    """Ручной ввод НМЦК (закрытые тендеры, где цена известна источнику, а не ТЗ)."""
    text = nmck.replace("\xa0", "").replace(" ", "").replace(",", ".").strip()
    try:
        value = Decimal(text)
    except InvalidOperation:
        return _detail(reestr_number, "НМЦК не распознана — введите число в рублях")
    if value <= 0:
        return _detail(reestr_number, "НМЦК должна быть больше нуля")
    with get_session_factory()() as session:
        found = WebRepository(session).get(reestr_number)
        if found is None:
            return _detail(reestr_number, "Тендер не найден")
        tender = found[0]
        tender.nmck = value
        tender.currency = tender.currency or "RUB"
        session.add(tender)
        session.commit()
    return _detail(reestr_number, "НМЦК сохранена — расчёт теперь пойдёт от этой цены")


@router.get("/tender/{reestr_number}/economics/status")
def economics_status(request: Request, reestr_number: str) -> JSONResponse:
    """Прогресс расчёта ИМЕННО этого тендера (для прогресс-бара на карточке)."""
    s = eco_job.snapshot()
    running = s.running and s.reestr_number == reestr_number
    if not running:
        return JSONResponse({"running": False})
    progress, eta = time_progress(s.started_at, s.estimate_sec, min_fraction=s.phase_fraction)
    return JSONResponse({"running": True, "progress": progress, "eta": eta, "phase": s.phase})


def _parse_editor_state(raw: dict[str, Any]) -> dict[str, Any]:
    """Санитизация состояния редактора из браузера: числа, idx, touched."""

    def rows(items: object) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            idx = item.get("idx")
            touched = item.get("touched")
            out.append(
                {
                    "idx": int(idx) if isinstance(idx, int | float) and idx is not None else None,
                    "name": str(item.get("name") or ""),
                    "canon": str(item["canon"]) if item.get("canon") else None,
                    "amount": _to_float(item.get("amount")),
                    "share_pct": _to_float(item.get("share_pct")),
                    "touched": touched if touched in ("amount", "share_pct") else None,
                }
            )
        return out

    base_any = raw.get("base")
    base_raw: dict[str, Any] = base_any if isinstance(base_any, dict) else {}
    params_any = raw.get("params")
    params_raw: dict[str, Any] = params_any if isinstance(params_any, dict) else {}
    state: dict[str, Any] = {
        "lines": rows(raw.get("lines")),
        "overheads": rows(raw.get("overheads")),
        "base": {
            "object_kind": str(base_raw.get("object_kind") or "") or None,
            "design_stage": str(base_raw.get("design_stage") or "") or None,
        },
        "params": {
            "min_margin_pct": _to_float(params_raw.get("min_margin_pct")),
            "target_margin_pct": _to_float(params_raw.get("target_margin_pct")),
        },
    }
    if "nmck" in base_raw:  # ключ есть -> НМЦК правили (None = очистили, режим предложения)
        state["base"]["nmck"] = _to_float(base_raw.get("nmck"))
    return state


def _editor_payload(
    session_store: EconomicsStore, payload: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    """Применить состояние редактора: медианы канонов по аналогам расчёта -> движок."""
    analog_ids = {a.get("id") for a in payload.get("analogs", [])}
    analogs = [p for p in session_store.analog_projects() if p.id in analog_ids]
    nmck = state.get("base", {}).get("nmck", payload.get("base", {}).get("nmck"))
    occupied = occupied_design_canons(row.get("canon") for row in state.get("lines", []))
    medians = (
        canon_median_hints(analogs, nmck=nmck, occupied_design=occupied) if analogs else {}
    )
    return apply_editor_state(payload, state, canon_medians=medians)


@router.post("/tender/{reestr_number}/economics/{calc_id}/preview")
async def preview_economics(request: Request, reestr_number: str, calc_id: int) -> JSONResponse:
    """Живой пересчёт для редактора: состояние -> новый payload БЕЗ сохранения."""
    raw = await request.json()
    with get_session_factory()() as session:
        store = EconomicsStore(session)
        calc = store.get(reestr_number, calc_id)
        if calc is None:
            return JSONResponse({"error": "Расчёт не найден"}, status_code=404)
        new_payload = _editor_payload(store, dict(calc.payload), _parse_editor_state(raw))
    return JSONResponse({"payload": new_payload})


@router.post("/tender/{reestr_number}/economics/{calc_id}/save")
async def save_economics(request: Request, reestr_number: str, calc_id: int) -> JSONResponse:
    """Сохранить состояние редактора новой версией (append-only). НМЦК -> в тендер."""
    raw = await request.json()
    state = _parse_editor_state(raw)
    with get_session_factory()() as session:
        store = EconomicsStore(session)
        calc = store.get(reestr_number, calc_id)
        if calc is None:
            return JSONResponse({"error": "Расчёт не найден"}, status_code=404)
        new_payload = _editor_payload(store, dict(calc.payload), state)
        new_nmck = new_payload.get("base", {}).get("nmck")
        found = WebRepository(session).get(reestr_number)
        if found is not None and new_nmck is not None:
            tender = found[0]
            if tender.nmck is None or abs(float(tender.nmck) - float(new_nmck)) > 0.004:
                tender.nmck = Decimal(str(new_nmck))
                tender.currency = tender.currency or "RUB"
                session.add(tender)
        store.add_calculation(reestr_number, created_by="user", model=None, payload=new_payload)
    return JSONResponse(
        {"ok": True, "redirect": f"/tender/{reestr_number}?{_MSG_SAVED}#economics"}
    )


_MSG_SAVED = urlencode({"msg": "Правки сохранены, итоги пересчитаны (новая версия)"})


@router.post("/tender/{reestr_number}/economics/{calc_id}/restore")
def restore_economics(request: Request, reestr_number: str, calc_id: int) -> RedirectResponse:
    """Откат: выбранная версия копируется наверх новой версией (история не трогается)."""
    with get_session_factory()() as session:
        store = EconomicsStore(session)
        calc = store.get(reestr_number, calc_id)
        if calc is None:
            return _detail(reestr_number, "Расчёт не найден")
        store.add_calculation(
            reestr_number, created_by="user", model=calc.model, payload=dict(calc.payload)
        )
    return _detail(reestr_number, f"Версия №{calc_id} восстановлена (скопирована новой версией)")


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
