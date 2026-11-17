import pytest
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI

from app.config import Settings
from app.llm import LLM_MAX_RETRIES, LLM_TIMEOUT_SECONDS, get_llm
from tests.test_config import FAKE_ENV


@pytest.fixture
def base_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


def build(**overrides: str) -> Settings:
    # _env_file=None isolates the test from any real .env on the developer's machine.
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_google_is_the_default_provider(base_env: pytest.MonkeyPatch) -> None:
    llm = get_llm(build())
    assert isinstance(llm, ChatGoogleGenerativeAI)
    assert llm.model.endswith("gemini-3.8-flash")


def test_anthropic_provider_returns_claude_model(base_env: pytest.MonkeyPatch) -> None:
    llm = get_llm(build(llm_provider="anthropic"))
    assert isinstance(llm, ChatAnthropic)
    assert llm.model == "claude-sonnet-5-5"


def test_calls_are_bounded_by_timeout_and_retry_limits(
    base_env: pytest.MonkeyPatch,
) -> None:
    google = get_llm(build(llm_provider="google"))
    anthropic = get_llm(build(llm_provider="anthropic"))
    assert isinstance(google, ChatGoogleGenerativeAI)
    assert isinstance(anthropic, ChatAnthropic)
    assert google.timeout == LLM_TIMEOUT_SECONDS
    assert google.max_retries == LLM_MAX_RETRIES
    assert anthropic.default_request_timeout == LLM_TIMEOUT_SECONDS
    assert anthropic.max_retries == LLM_MAX_RETRIES


def test_explicit_model_overrides_the_default(base_env: pytest.MonkeyPatch) -> None:
    llm = get_llm(build(llm_provider="google", llm_model="gemini-2.5-flash-lite"))
    assert llm.model.endswith("gemini-2.5-flash-lite")
