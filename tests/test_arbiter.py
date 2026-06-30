from tender_ingest.config import Settings
from tender_ingest.relevance.arbiter import create_arbiter
from tender_ingest.relevance.arbiter.mock import MockArbiter
from tender_ingest.relevance.arbiter.prompt import parse_response


def test_factory_defaults_to_mock() -> None:
    arb = create_arbiter(Settings(arbiter_provider="mock"))
    assert isinstance(arb, MockArbiter)
    assert arb.provider == "mock"


def test_mock_decides_relevant_on_design_signal() -> None:
    v = MockArbiter().decide("Корректировка проектной документации")
    assert v.relevant is True
    assert v.provider == "mock"


def test_mock_decides_not_relevant_without_signal() -> None:
    v = MockArbiter().decide("Поставка канцелярских товаров")
    assert v.relevant is False


def test_yandex_requires_keys() -> None:
    import pytest

    with pytest.raises(ValueError):
        create_arbiter(Settings(arbiter_provider="yandex"))


def test_unknown_provider_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        create_arbiter(Settings(arbiter_provider="gigachat"))


def test_parse_response() -> None:
    assert parse_response("ДА|проектная документация") == (True, "проектная документация")
    assert parse_response("НЕТ|это поставка")[0] is False
    assert parse_response("YES|design docs")[0] is True
