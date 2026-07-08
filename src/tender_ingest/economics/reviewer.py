"""ИИ-ревью готового расчёта экономики: оценка реальности + открытые источники.

Запускается ПОСЛЕ детерминированного расчёта (engine). Модель с веб-поиском смотрит
на готовую таблицу и оценивает: какие строки занижены/завышены относительно рынка
(изыскания, субподряд, экспертизы), чего в расчёте не хватает, какая цена подачи
реалистична. Рекомендации НИЧЕГО не меняют в расчёте сами — они сохраняются в
payload['review'] и применяются только явной кнопкой (через engine.apply_edits).

Ответ модели — JSON в тексте (веб-поиск и structured output вместе ненадёжны),
парсим устойчиво; сломался парс — сохраняем сырой текст, расчёт не страдает.
"""

from __future__ import annotations

import json
import re
from typing import Any

import anthropic
import structlog

from tender_ingest.config import MissingApiKeyError, Settings, get_settings
from tender_ingest.llm_retry import call_with_retries

log = structlog.get_logger()

# Ревью с веб-поиском при effort=high тратит заметную долю на thinking (входит
# в max_tokens у Sonnet 5) — держим запас.
_MAX_TOKENS = 16000
_MAX_WEB_SEARCHES = 6

REVIEW_SYSTEM = """Ты — независимый аудитор экономики проектного бюро «КРАФТ ГРУПП» \
(архитектурное проектирование: ПД/РД, благоустройство, ОКН; регионы УрФО и Тюменская \
область). Тебе дают ГОТОВЫЙ расчёт себестоимости тендера, посчитанный алгоритмом по \
медианным долям из прошлых проектов бюро.

ЗАДАЧА: оценить, насколько расчёт реален, опираясь И на данные бюро, И на ОТКРЫТЫЕ \
ИСТОЧНИКИ (веб-поиск): рыночные расценки на инженерные изыскания, разделы ПД, \
обследования, экспертизу, субподряд по РФ в текущих ценах. Где расходы занижены — \
скажи прямо и предложи сколько реально; где завышены и можно сэкономить — тоже. \
Ничего не подгоняй под «красивый» итог: если участие невыгодно — так и пиши.

ПРАВИЛА:
- Каждую рекомендацию обосновывай: цифра из открытого источника (укажи источник/URL) \
или логика из данных тендера. Без основания — не предлагай.
- suggested_amount указывай ТОЛЬКО когда уверен в конкретной сумме (₽); иначе null и \
assessment='verify' (проверить вручную).
- Строки «нет данных» — постарайся оценить по рынку (это самое ценное).
- Учитывай регион, масштаб объекта и сроки из карточки.

ОТВЕТ — СТРОГО один JSON-объект без пояснений вокруг, схема:
{
 "overall": {"verdict": "realistic"|"optimistic"|"understated",
             "summary": "3–6 предложений: общий вывод о реальности расчёта"},
 "adjustments": [{"key": "l0"|"o1"|…(ключ строки из расчёта),
                  "name": "название строки",
                  "assessment": "ok"|"increase"|"decrease"|"verify",
                  "suggested_amount": число ₽ или null,
                  "reasoning": "почему, с цифрами",
                  "source": "URL или название источника, пусто если из данных бюро"}],
 "missing_costs": [{"name": "чего не хватает в расчёте", "estimate": число ₽ или null,
                    "reasoning": "...", "source": "..."}],
 "market_notes": [{"note": "рыночный ориентир с цифрами", "source": "URL/название"}],
 "suggested_price": {"price": число ₽ или null,
                     "rationale": "обоснование рекомендованной цены подачи"}
}"""


def _money(value: object) -> str:
    if value is None:
        return "—"
    return f"{float(str(value)):,.0f} ₽".replace(",", " ")


def _payload_digest(payload: dict[str, Any]) -> str:
    """Компактное текстовое представление расчёта для ревью (оба режима базы)."""
    base = payload["base"]
    totals = payload["totals"]
    offer_mode = base.get("nmck") is None
    lines = []
    for prefix, bucket in (("l", payload["lines"]), ("o", payload["overheads"])):
        for i, line in enumerate(bucket):
            amount = _money(line.get("amount")) if line.get("amount") else "НЕТ ДАННЫХ"
            share = f"{line['share_pct']}%" if line.get("share_pct") is not None else "—"
            lines.append(
                f"[{prefix}{i}] {line['name']} | доля {share} | {amount} | "
                f"источник: {line.get('source')} | {line.get('note') or ''}"
            )
    if offer_mode:
        head = (
            "НМЦК НЕ УКАЗАНА — цена сформирована от себестоимости "
            f"(предложение компании: {_money(totals.get('price'))}, "
            f"маржа {totals.get('margin_pct')}%). Особо проверь реальность сумм по рынку.\n"
        )
        grid = (
            "Варианты цены по маржам: "
            + "; ".join(
                f"маржа {s['margin_pct']}% -> цена {_money(s['price'])}"
                for s in payload.get("scenarios", [])
            )
            + "\n"
        )
    else:
        head = f"НМЦК (база): {_money(base['nmck'])}\n"
        grid = (
            "Сетка понижения: "
            + "; ".join(
                f"-{s['reduction_pct']}% -> прибыль {_money(s['profit'])}"
                for s in payload.get("scenarios", [])
            )
            + "\n"
        )
    profit = totals.get("profit_at_nmck", totals.get("profit_at_offer"))
    analogs = "; ".join(str(a["title"]) for a in payload.get("analogs", []))
    return (
        head
        + "СТРОКИ РАСЧЁТА (ключ | название | доля | сумма | источник):\n"
        + "\n".join(lines)
        + f"\nИТОГО себестоимость: {_money(totals['cost'])}; "
        + f"прибыль: {_money(profit)} ({totals.get('margin_pct')}%)\n"
        + grid
        + f"Мин. цена (маржа {payload['min_price']['min_margin_pct']}%): "
        + f"{_money(payload['min_price']['price'])}\n"
        + f"Проекты-аналоги: {analogs}\n"
        + "Предупреждения: "
        + "; ".join(payload.get("warnings", []))
    )


def _extract_json(text: str) -> dict[str, Any] | None:
    """Достать первый JSON-объект из текста ответа (модель может добавить обвязку)."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


class EconomicsReviewer:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def review(
        self, *, payload: dict[str, Any], card_context: str, brief: dict[str, Any]
    ) -> dict[str, Any]:
        """Оценка реальности расчёта. Возвращает объект review (см. схему в промпте).

        brief передаётся ЦЕЛИКОМ (все поля, findings, work_breakdown с цитатами из ТЗ) —
        ревью опирается на всё, что вытащено из документации, а не только на резюме.
        """
        message = (
            "Оцени реальность расчёта экономики тендера. Используй веб-поиск для рыночных "
            "расценок (изыскания, разделы ПД, обследования, экспертиза, субподряд, РФ, "
            "текущий год) И полный бриф по ТЗ ниже (площади, объёмы, сроки, особые условия "
            "— всё влияет на стоимость). Верни ТОЛЬКО JSON по схеме.\n\n"
            "=== КАРТОЧКА ЗАКУПКИ (со скорингом) ===\n" + card_context + "\n\n"
            "=== ПОЛНЫЙ БРИФ ПО ТЗ (всё, что вытащено из документации, с цитатами) ===\n"
            + json.dumps(brief, ensure_ascii=False)
            + "\n\n"
            "=== РАСЧЁТ АЛГОРИТМА ===\n" + _payload_digest(payload)
        )
        # stream: большие max_tokens + веб-поиск — SDK требует стриминг для долгих запросов
        def _call() -> anthropic.types.Message:
            with self._client.messages.stream(
                model=self._model,
                max_tokens=_MAX_TOKENS,
                system=[
                    {"type": "text", "text": REVIEW_SYSTEM, "cache_control": {"type": "ephemeral"}}
                ],
                messages=[{"role": "user", "content": message}],
                output_config={"effort": "high"},
                tools=[
                    {
                        "type": "web_search_20250305",
                        "name": "web_search",
                        "max_uses": _MAX_WEB_SEARCHES,
                    }
                ],
            ) as stream:
                return stream.get_final_message()

        resp = call_with_retries(_call, label="economics_reviewer")
        text = "".join(getattr(block, "text", "") for block in resp.content)
        usage = resp.usage
        log.info(
            "economics_reviewed",
            model=self._model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
        review = _extract_json(text)
        if review is None:
            # Парс не удался — сохраняем сырой текст, чтобы не потерять анализ.
            return {"overall": {"verdict": "verify", "summary": text[:4000]}, "adjustments": []}
        review.setdefault("adjustments", [])
        return review


def create_economics_reviewer(settings: Settings | None = None) -> EconomicsReviewer:
    cfg = settings or get_settings()
    if not cfg.anthropic_api_key:
        raise MissingApiKeyError("Нужен ANTHROPIC_API_KEY для ИИ-ревью экономики")
    return EconomicsReviewer(api_key=cfg.anthropic_api_key, model=cfg.claude_model)
