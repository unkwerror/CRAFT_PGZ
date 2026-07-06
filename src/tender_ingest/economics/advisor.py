"""EconomicsAdvisor — рекомендация по цене/участию на Claude (structured output).

Системный промпт (профиль бюро + правила экономики) кэшируется (cache_control) —
одинаков для всех тендеров, повторные запросы читают его ~10x дешевле. Уникальна
только пользовательская часть: карточка + агрегаты + кейсы.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic
import structlog

from tender_ingest.config import Settings, get_settings
from tender_ingest.economics.cases import CaseCorpus
from tender_ingest.economics.prompt import RECO_SCHEMA, SYSTEM_PROMPT, build_message

log = structlog.get_logger()

_MAX_TOKENS = 4000  # rationale + риски + стратегия: рекомендация компактная


class EconomicsAdvisor:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def recommend(self, corpus: CaseCorpus) -> dict[str, Any]:
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=_MAX_TOKENS,
            system=[
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
            ],
            messages=[
                {
                    "role": "user",
                    "content": build_message(
                        corpus.target_block,
                        corpus.aggregates_block,
                        corpus.cases_block,
                        corpus.feedback_block,
                    ),
                }
            ],
            output_config={"format": {"type": "json_schema", "schema": RECO_SCHEMA}},
        )
        raw = "".join(getattr(block, "text", "") for block in resp.content)
        data: dict[str, Any] = json.loads(raw)
        u = resp.usage
        log.info(
            "economics_recommended",
            model=self._model,
            n_cases=corpus.n_cases,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read=getattr(u, "cache_read_input_tokens", 0),
        )
        return data


def create_economics_advisor(settings: Settings | None = None) -> EconomicsAdvisor:
    """Собрать экономиста из конфигурации. Без ключа -> ValueError."""
    cfg = settings or get_settings()
    if not cfg.anthropic_api_key:
        raise ValueError("Нужен ANTHROPIC_API_KEY для ИИ-экономиста (Claude)")
    return EconomicsAdvisor(api_key=cfg.anthropic_api_key, model=cfg.claude_model)
