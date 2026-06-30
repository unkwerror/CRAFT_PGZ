from decimal import Decimal
from pathlib import Path

import pytest

from tender_ingest.sources.base import RawTender
from tender_ingest.sources.excel_source import SOURCE_NAME, ExcelSource

FIXTURE = Path(__file__).parent / "fixtures" / "Контур.Закупки_30.06.2026.xlsx"


@pytest.fixture(scope="module")
def tenders() -> list[RawTender]:
    return list(ExcelSource(FIXTURE).fetch())


def test_fixture_exists() -> None:
    assert FIXTURE.exists(), "Положи реальную выгрузку Контура в tests/fixtures/"


def test_row_count(tenders: list[RawTender]) -> None:
    # в образце ~160 непустых строк; хвостовые пустые строки отброшены
    assert len(tenders) == 160


def test_all_have_string_number_and_source(tenders: list[RawTender]) -> None:
    for t in tenders:
        assert isinstance(t.reestr_number, str) and t.reestr_number
        assert t.source == SOURCE_NAME


def test_alphanumeric_numbers_preserved(tenders: list[RawTender]) -> None:
    numbers = {t.reestr_number for t in tenders}
    assert "B2606261843476" in numbers  # коммерческий буквенно-цифровой
    assert "0373200597926000045" in numbers  # 44-ФЗ с ведущим нулём


def test_first_row_fields(tenders: list[RawTender]) -> None:
    first = next(t for t in tenders if t.reestr_number == "0373200597926000045")
    assert first.law == "44-ФЗ"
    assert first.nmck == Decimal("16479926.97")
    assert first.currency == "RUB"
    assert first.region_code == "77"
    assert first.region_name == "Москва"
    assert first.customer_inn == "7704076129"
    assert first.publish_date is not None
    # обеспечение заявки — рубли
    assert first.securities["bid"].amount_rub == Decimal("164799.27")
    # аванс — процент
    assert first.advance_pct == Decimal("20.00")


def test_raw_payload_present(tenders: list[RawTender]) -> None:
    first = tenders[0]
    assert first.raw  # сырьё строки сохранено целиком
    assert len(first.raw) == 34


def test_laws_distribution(tenders: list[RawTender]) -> None:
    laws = {t.law for t in tenders}
    assert {"44-ФЗ", "223-ФЗ", "Коммерческие", "615 ПП"} <= laws


def test_wrong_format_raises(tmp_path: Path) -> None:
    import openpyxl

    bad = tmp_path / "bad.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["foo"])
    ws.append(["bar"])
    wb.save(bad)
    from tender_ingest.sources.excel_source import ExcelFormatError

    with pytest.raises(ExcelFormatError):
        list(ExcelSource(bad).fetch())
