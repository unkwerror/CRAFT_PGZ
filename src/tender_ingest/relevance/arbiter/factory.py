"""Выбор арбитра по конфигурации (ARBITER_PROVIDER). По умолчанию — Mock (без ключа)."""

from __future__ import annotations

from tender_ingest.config import Settings, get_settings
from tender_ingest.relevance.arbiter.base import RelevanceArbiter
from tender_ingest.relevance.arbiter.mock import MockArbiter


def create_arbiter(settings: Settings | None = None) -> RelevanceArbiter:
    cfg = settings or get_settings()
    provider = cfg.arbiter_provider.lower()

    if provider == "mock":
        return MockArbiter()

    if provider == "yandex":
        if not cfg.yandex_api_key or not cfg.yandex_folder_id:
            raise ValueError("Для ARBITER_PROVIDER=yandex нужны YANDEX_API_KEY и YANDEX_FOLDER_ID")
        # Импорт здесь, чтобы httpx не требовался при работе на Mock.
        from tender_ingest.relevance.arbiter.yandex import YandexGPTArbiter

        return YandexGPTArbiter(api_key=cfg.yandex_api_key, folder_id=cfg.yandex_folder_id)

    raise ValueError(f"Неизвестный ARBITER_PROVIDER: {cfg.arbiter_provider!r}")
