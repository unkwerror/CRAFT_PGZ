"""Тесты веба без БД: здоровье, аутентификация, чистые хелперы.

Страницы со списком/карточкой ходят в Postgres — их проверяем на сервере после
деплоя. Здесь — то, что не требует БД (auth-флоу, фильтры, проверка пароля).
"""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from tender_ingest.web.app import app
from tender_ingest.web.repository import Filters
from tender_ingest.web.security import check_password

client = TestClient(app)


def test_health() -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_root_requires_auth_redirects_to_login() -> None:
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_score_requires_auth() -> None:
    r = client.post("/score", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_document_download_requires_auth() -> None:
    r = client.get("/tender/x/documents/1", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_login_page_renders() -> None:
    r = client.get("/login")
    assert r.status_code == 200
    assert "пароль" in r.text.lower()


def test_login_wrong_password_401() -> None:
    r = client.post("/login", data={"password": "nope"}, follow_redirects=False)
    assert r.status_code == 401


def test_login_correct_sets_session_and_redirects() -> None:
    # дефолтный web_password = "craft"
    c = TestClient(app)
    r = c.post("/login", data={"password": "craft"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert "craft_session" in r.headers.get("set-cookie", "")


def test_check_password() -> None:
    assert check_password("craft", "craft") is True
    assert check_password("x", "craft") is False
    assert check_password("anything", "") is False  # пустой ожидаемый -> отказ


def test_filters_from_query_cleans_garbage() -> None:
    f = Filters.from_query(verdict="bogus", sort="bogus", search="  ", page="-5")
    assert f.verdict is None
    assert f.sort == "score"
    assert f.search is None
    assert f.page == 1


def test_filters_from_query_parses_ranges_and_selects() -> None:
    f = Filters.from_query(
        search="проектная документация",
        exclude=" поставка аренда ",
        verdict="relevant",
        region_code="72",
        law="44-ФЗ",
        nmck_min="1 000 000",
        nmck_max="150000000",
        exact="1",
    )
    assert f.nmck_min == Decimal("1000000")
    assert f.nmck_max == Decimal("150000000")
    assert f.exclude == "поставка аренда"
    assert f.verdict == "relevant"
    assert f.region_code == "72"
    assert f.law == "44-ФЗ"
    assert f.exact is True


def test_filters_bad_nmck_dropped() -> None:
    f = Filters.from_query(nmck_min="не-число", nmck_max="")
    assert f.nmck_min is None
    assert f.nmck_max is None


def test_filters_upload_parsed() -> None:
    assert Filters.from_query(upload="3").upload == 3
    assert Filters.from_query(upload="мусор").upload is None
    assert Filters.from_query().upload is None
