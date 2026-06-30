import datetime as dt
from decimal import Decimal

from tender_ingest.normalize import (
    clean_str,
    parse_date,
    parse_money,
    parse_number,
    parse_security,
    split_region,
)


def test_clean_str() -> None:
    assert clean_str("") is None
    assert clean_str("  ") is None
    assert clean_str(None) is None
    assert clean_str("  abc ") == "abc"


def test_parse_number_keeps_string() -> None:
    # ведущие нули и буквенные ID не теряем
    assert parse_number("0373200597926000045") == "0373200597926000045"
    assert parse_number("B2606261843476") == "B2606261843476"
    assert parse_number("ГП642268") == "ГП642268"
    assert parse_number(209695) == "209695"
    assert parse_number("") is None


def test_parse_money() -> None:
    assert parse_money(16479926.97) == Decimal("16479926.97")
    assert parse_money(25000000) == Decimal("25000000")
    assert parse_money("1 324 918,96") == Decimal("1324918.96")
    assert parse_money("") is None
    assert parse_money("не число") is None


def test_parse_date_datetime_passthrough() -> None:
    d = dt.datetime(2026, 7, 16, 9, 0)
    assert parse_date(d) == d


def test_parse_date_excel_serial_with_guard() -> None:
    # 46203 ~ внутри диапазона -> валидная дата
    assert parse_date(46203) is not None
    # за пределами guard -> None (мусор не превращаем в дату)
    assert parse_date(5) is None
    assert parse_date(999999) is None
    assert parse_date("") is None


def test_parse_date_iso_string() -> None:
    assert parse_date("2026-06-30") == dt.datetime(2026, 6, 30)
    assert parse_date("мусор") is None


def test_split_region() -> None:
    assert split_region("77 Москва") == ("77", "Москва")
    assert split_region("01 Республика Адыгея") == ("01", "Республика Адыгея")
    assert split_region("25 Приморский край") == ("25", "Приморский край")
    assert split_region("") == (None, None)
    assert split_region("Без кода") == (None, "Без кода")


def test_parse_security_rubles() -> None:
    sec = parse_security(164799.27)
    assert sec.amount_rub == Decimal("164799.27")
    assert sec.percent is None
    assert sec.raw == "164799.27"


def test_parse_security_percent() -> None:
    sec = parse_security("30.00 %")
    assert sec.percent == Decimal("30.00")
    assert sec.amount_rub is None
    assert sec.raw == "30.00 %"


def test_parse_security_freeform() -> None:
    sec = parse_security("Есть. См. документацию")
    assert sec.raw == "Есть. См. документацию"
    assert sec.amount_rub is None
    assert sec.percent is None


def test_parse_security_empty() -> None:
    sec = parse_security("")
    assert sec == (None, None, None)
