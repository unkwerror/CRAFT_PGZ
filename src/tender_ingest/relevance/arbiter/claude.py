"""ClaudeArbiter — многофакторный скоринг закупок на Claude (Anthropic API).

Оценивает закупки ПАЧКОЙ: карточки со всеми полями идут в один запрос, на выходе —
массив {reestr_number, score, summary} (structured output, строгий JSON). System-промпт
с профилем и рубрикой кэшируется. При сбое/пропуске строки — score 0 с пометкой, чтобы
прогон не падал и каждая закупка получила запись.
"""

from __future__ import annotations

import json

import anthropic
import structlog

from tender_ingest.relevance.arbiter.base import NOT_SCORED, ArbiterVerdict, RelevanceArbiter
from tender_ingest.relevance.arbiter.prompt import BATCH_SCHEMA, SYSTEM_PROMPT, build_batch_message

log = structlog.get_logger()

_MAX_TOKENS = 8000  # хватает на пачку (до ~20 карточек по короткому резюме)


class ClaudeArbiter(RelevanceArbiter):
    provider = "claude"

    def __init__(self, api_key: str, model: str) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def _verdict(self, score: int, summary: str) -> ArbiterVerdict:
        return ArbiterVerdict(
            score=max(0, min(100, int(score))),
            summary=str(summary).strip(),
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
                system=[
                    {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
                ],
                messages=[{"role": "user", "content": build_batch_message(items)}],
                output_config={"format": {"type": "json_schema", "schema": BATCH_SCHEMA}},
            )
            text = "".join(getattr(block, "text", "") for block in resp.content)
            for item in json.loads(text)["results"]:
                by_reestr[str(item["reestr_number"])] = self._verdict(
                    item["score"], item["summary"]
                )
        except (
            anthropic.APIError,
            anthropic.APIConnectionError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            log.warning("claude_batch_failed", size=len(items), error=str(exc))

        # Гарантируем запись для каждой запрошенной карточки (пропуски -> 0).
        fallback = ArbiterVerdict(score=0, summary=NOT_SCORED, provider=self.provider)
        return {reestr: by_reestr.get(reestr, fallback) for reestr, _ in items}
