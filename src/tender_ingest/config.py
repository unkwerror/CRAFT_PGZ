"""Чтение конфигурации из окружения/.env (CLAUDE.md, раздел 10).

Секретов у источника v1 нет — файл выгрузки кладёт пользователь. Здесь только
строка подключения к БД и уровень логирования. Доступы появятся, когда добавим
EmailSource/DamiaSource (будущие фазы).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Для docker compose host = postgres; для локального запуска -> localhost.
    database_url: str = "postgresql+psycopg://tender:tender@localhost:5432/tender"
    log_level: str = "INFO"

    # LLM-арбитр релевантности (Фаза 1). По умолчанию mock — без ключа и сети.
    arbiter_provider: str = "mock"  # mock | yandex
    yandex_api_key: str = ""
    yandex_folder_id: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
