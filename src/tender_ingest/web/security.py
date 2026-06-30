"""Аутентификация веба: общий пароль на бюро + сессия-cookie.

Простая модель (Фаза 2): один пароль, проверка через hmac.compare_digest
(защита от timing-атак), факт входа хранится в подписанной сессии-cookie.
Позже легко заменить на аккаунты пользователей, не трогая роуты.
"""

from __future__ import annotations

import hmac

from fastapi import Request


class NotAuthenticatedError(Exception):
    """Бросается зависимостью require_auth -> обработчик редиректит на /login."""


def check_password(plain: str, expected: str) -> bool:
    """Сравнение постоянного времени; пустой ожидаемый пароль -> всегда отказ."""
    if not expected:
        return False
    return hmac.compare_digest(plain.encode("utf-8"), expected.encode("utf-8"))


def is_authenticated(request: Request) -> bool:
    return bool(request.session.get("auth"))


def require_auth(request: Request) -> None:
    """FastAPI-зависимость: пускает только вошедших, иначе -> NotAuthenticatedError."""
    if not is_authenticated(request):
        raise NotAuthenticatedError
