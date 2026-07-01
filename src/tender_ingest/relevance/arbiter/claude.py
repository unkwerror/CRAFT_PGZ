"""ClaudeArbiter — арбитр релевантности на Claude (Anthropic API), prod.

Требует ANTHROPIC_API_KEY. Модель по умолчанию
claude-opus-4-8 (настраивается CLAUDE_MODEL). Это простая классификация
«релевантно/нет» одним вызовом — без extended thinking. При сбое сети/ошибке НЕ
роняем прогон: relevant=False с пометкой ошибки, закупка остаётся на ручной пересмотр.
"""

from __future__ import annotations

import anthropic
import structlog

from tender_ingest.relevance.arbiter.base import ArbiterVerdict, RelevanceArbiter
from tender_ingest.relevance.arbiter.prompt import SYSTEM_PROMPT, parse_response

log = structlog.get_logger()

_MAX_TOKENS = 200


class ClaudeArbiter(RelevanceArbiter):
    provider = "claude"

    def __init__(self, api_key: str, model: str) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def decide(self, subject: str) -> ArbiterVerdict:
        try:
            resp = self._client.messages.create(
                model=self._model,
                max_tokens=_MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": subject}],
            )
            text = "".join(getattr(block, "text", "") for block in resp.content)
        except (anthropic.APIError, anthropic.APIConnectionError) as exc:
            log.warning("claude_arbiter_failed", error=str(exc))
            return ArbiterVerdict(
                relevant=False, reason=f"claude: ошибка вызова ({exc})", provider=self.provider
            )

        relevant, reason = parse_response(text)
        return ArbiterVerdict(relevant=relevant, reason=f"claude: {reason}", provider=self.provider)
