"""Чистые функции трекинга: разбор форм участия, фильтр избранного."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from tender_ingest.web.repository import Filters
from tender_ingest.web.routes.tracking import _date, _price
from tender_ingest.web.tracking import PARTICIPATION_STATUSES, STATUS_LABELS


def test_filters_fav_parsed() -> None:
    assert Filters.from_query(fav="1").fav is True
    assert Filters.from_query(fav="").fav is False
    assert Filters.from_query().fav is False


def test_price_parsing_forgiving() -> None:
    assert _price("8 200 000") == Decimal("8200000")
    assert _price("8200000,50") == Decimal("8200000.50")
    assert _price("не число") is None
    assert _price("") is None
    assert _price(None) is None


def test_date_parsing_forgiving() -> None:
    assert _date("2026-07-01") == dt.date(2026, 7, 1)
    assert _date("кривая дата") is None
    assert _date(None) is None


def test_status_labels_cover_all_statuses() -> None:
    assert set(STATUS_LABELS) == set(PARTICIPATION_STATUSES)
