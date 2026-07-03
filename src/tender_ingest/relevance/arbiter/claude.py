"""ClaudeArbiter — многофакторный скоринг закупок на Claude (Anthropic API).

Оценивает закупки ПАЧКОЙ: карточки со всеми полями идут в один запрос, на выходе —
массив {reestr_number, reasoning, red_flags, score, confidence, summary} (structured
output, строгий JSON). При сбое/пропуске строки — score из fallback с пометкой, чтобы
прогон не падал и каждая закупка получила запись.

PROMPT CACHING: системный промпт (~2.4k токенов: профиль КРАФТ + рубрика) помечен
cache_control=ephemeral. Он одинаков для всех карточек, поэтому кэшируется один раз, а
последующие батчи в прогоне читают его в ~10× дешевле. На Sonnet работает (минимальный
кэшируемый префикс 1024 токена < 2.4k); на Opus не кэшировался (там порог 4096).
"""

from __future__ import annotations

import json
from typing import Any

import anthropic
import structlog

from tender_ingest.relevance.arbiter.base import (
    NOT_SCORED,
    NOT_SCORED_SCORE,
    ArbiterVerdict,
    RelevanceArbiter,
)
from tender_ingest.relevance.arbiter.prompt import BATCH_SCHEMA, SYSTEM_PROMPT, build_batch_message

log = structlog.get_logger()

_MAX_TOKENS = 8000  # хватает на пачку (до ~20 карточек: reasoning ≤40 слов + summary)


class ClaudeArbiter(RelevanceArbiter):
    provider = "claude"

    def __init__(self, api_key: str, model: str) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def _verdict(self, item: dict[str, Any]) -> ArbiterVerdict:
        raw_flags = item.get("red_flags") or []
        flags = [str(x).strip() for x in raw_flags if str(x).strip()]
        return ArbiterVerdict(
            score=max(0, min(100, int(item["score"]))),
            summary=str(item["summary"]).strip(),
            reasoning=str(item.get("reasoning") or "").strip(),
            confidence=max(0, min(100, int(item.get("confidence") or 0))),
            red_flags=flags,
            provider=self.provider,
        )

    def decide_batch(self, items: list[tuple[str, str]]) -> dict[str, ArbiterVerdict]:
        if not items:
            return {}
        by_reestr: dict[str, ArbiterVerdict] = {}
        try:
            resp = self._client.messages.create(
                model=self._model,
                max_tokens=_MAX_TOKENS,
                # cache_control кэширует системный промпт: первый батч прогона его пишет,
                # остальные читают дёшево (TTL 5 мин обновляется на каждом чтении, поэтому
                # держится весь прогон). Только карточки в user-сообщении уникальны.
                system=[
                    {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
                ],
                messages=[{"role": "user", "content": build_batch_message(items)}],
                output_config={"format": {"type": "json_schema", "schema": BATCH_SCHEMA}},
            )
            text = "".join(getattr(block, "text", "") for block in resp.content)
            for item in json.loads(text)["results"]:
                by_reestr[str(item["reestr_number"])] = self._verdict(item)
        except (
            anthropic.APIError,
            anthropic.APIConnectionError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            log.warning("claude_batch_failed", size=len(items), error=str(exc))

        # Пропущенные/сбойные -> зона «возможно» (не теряем тендер в шуме).
        fallback = ArbiterVerdict(
            score=NOT_SCORED_SCORE, summary=NOT_SCORED, provider=self.provider
        )
        return {reestr: by_reestr.get(reestr, fallback) for reestr, _ in items}
