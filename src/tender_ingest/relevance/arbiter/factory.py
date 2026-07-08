"""Создание LLM-арбитра релевантности. Единственный провайдер — Claude (Anthropic)."""

from __future__ import annotations

from tender_ingest.config import MissingApiKeyError, Settings, get_settings
from tender_ingest.relevance.arbiter.base import RelevanceArbiter


def create_arbiter(settings: Settings | None = None) -> RelevanceArbiter:
    cfg = settings or get_settings()
    if not cfg.anthropic_api_key:
        raise MissingApiKeyError("Нужен ANTHROPIC_API_KEY для арбитра релевантности (Claude)")
    # Импорт здесь, чтобы anthropic-SDK не тянулся при простом импорте фабрики.
    from tender_ingest.relevance.arbiter.claude import ClaudeArbiter

    return ClaudeArbiter(api_key=cfg.anthropic_api_key, model=cfg.claude_model)
