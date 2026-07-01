import pytest

from tender_ingest.config import Settings
from tender_ingest.relevance.arbiter import create_arbiter
from tender_ingest.relevance.arbiter.claude import ClaudeArbiter
from tender_ingest.relevance.arbiter.prompt import parse_response


def test_requires_api_key() -> None:
    with pytest.raises(ValueError):
        create_arbiter(Settings(anthropic_api_key=""))


def test_factory_builds_claude() -> None:
    arb = create_arbiter(Settings(anthropic_api_key="sk-test"))
    assert isinstance(arb, ClaudeArbiter)
    assert arb.provider == "claude"


def test_parse_response() -> None:
    assert parse_response("ДА|проектная документация") == (True, "проектная документация")
    assert parse_response("НЕТ|это поставка")[0] is False
    assert parse_response("YES|design docs")[0] is True
