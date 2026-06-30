"""YandexGPTArbiter — реальный арбитр на YandexGPT (prod).

Включается, когда ARBITER_PROVIDER=yandex и заданы YANDEX_API_KEY/YANDEX_FOLDER_ID.
Российский провайдер (без VPN), авторизация по Api-Key. temperature=0 — детерминизм.
При сбое сети/ответа НЕ роняем прогон: возвращаем relevant=False с пометкой ошибки —
закупка останется в «maybe-noise», аналитик пересмотрит вручную.
"""

from __future__ import annotations

import httpx
import structlog

from tender_ingest.relevance.arbiter.base import ArbiterVerdict, RelevanceArbiter
from tender_ingest.relevance.arbiter.prompt import SYSTEM_PROMPT, parse_response

log = structlog.get_logger()

_COMPLETION_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
_TIMEOUT = 30.0


class YandexGPTArbiter(RelevanceArbiter):
    provider = "yandex"

    def __init__(self, api_key: str, folder_id: str) -> None:
        self._api_key = api_key
        self._folder_id = folder_id
        self._model_uri = f"gpt://{folder_id}/yandexgpt/latest"

    def decide(self, subject: str) -> ArbiterVerdict:
        payload = {
            "modelUri": self._model_uri,
            "completionOptions": {"stream": False, "temperature": 0, "maxTokens": 200},
            "messages": [
                {"role": "system", "text": SYSTEM_PROMPT},
                {"role": "user", "text": subject},
            ],
        }
        headers = {
            "Authorization": f"Api-Key {self._api_key}",
            "x-folder-id": self._folder_id,
            "Content-Type": "application/json",
        }
        try:
            resp = httpx.post(_COMPLETION_URL, json=payload, headers=headers, timeout=_TIMEOUT)
            resp.raise_for_status()
            text = resp.json()["result"]["alternatives"][0]["message"]["text"]
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            log.warning("yandex_arbiter_failed", error=str(exc))
            return ArbiterVerdict(
                relevant=False, reason=f"yandex: ошибка вызова ({exc})", provider=self.provider
            )

        relevant, reason = parse_response(text)
        return ArbiterVerdict(relevant=relevant, reason=f"yandex: {reason}", provider=self.provider)
