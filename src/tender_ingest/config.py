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

    # LLM-арбитр релевантности (Claude / Anthropic API). Ключ обязателен для скоринга.
    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-4-8"

    # Веб-интерфейс (Фаза 2). Общий пароль на бюро + секрет подписи сессии-cookie.
    # В проде ОБЯЗАТЕЛЬНО задать через .env (дефолты — только для локалки).
    web_password: str = "craft"
    session_secret: str = "dev-insecure-change-me"
    session_https_only: bool = False  # в проде за HTTPS -> true (cookie только по TLS)
    max_upload_mb: int = 20  # лимит размера загружаемого .xlsx


@lru_cache
def get_settings() -> Settings:
    return Settings()
