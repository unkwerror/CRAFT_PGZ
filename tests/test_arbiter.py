import pytest

from tender_ingest.config import MissingApiKeyError, Settings
from tender_ingest.relevance.arbiter import create_arbiter
from tender_ingest.relevance.arbiter.claude import ClaudeArbiter


def test_requires_api_key() -> None:
    with pytest.raises(MissingApiKeyError):
        create_arbiter(Settings(anthropic_api_key=""))


def test_factory_builds_claude() -> None:
    arb = create_arbiter(Settings(anthropic_api_key="sk-test"))
    assert isinstance(arb, ClaudeArbiter)
    assert arb.provider == "claude"
