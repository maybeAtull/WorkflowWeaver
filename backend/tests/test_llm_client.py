from typing import Any

import pytest
from langchain_core.exceptions import OutputParserException
from pydantic import BaseModel

from app.agent.llm_client import (
    _USER_MESSAGES,
    LlmClient,
    LlmError,
    classify_llm_error,
)


class Answer(BaseModel):
    word: str


class FakeModel:
    """Stands in for a chat model: plays back scripted outcomes for each call."""

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.messages: list[Any] = []
        self.schemas: list[type] = []

    def with_structured_output(self, schema: type) -> "FakeModel":
        self.schemas.append(schema)
        return self

    async def ainvoke(self, messages: Any) -> Any:
        self.messages.append(messages)
        outcome = self.script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class StatusError(Exception):
    """Like a provider SDK error: carries an HTTP status code."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def client(model: FakeModel) -> tuple[LlmClient, list[float]]:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return LlmClient(lambda: model, fake_sleep, float), sleeps  # type: ignore[arg-type,return-value]


# ------------------------------------------------------------------ classification


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (TimeoutError(), "transient"),
        (StatusError("boom", 503), "transient"),
        (StatusError("boom", 529), "transient"),
        (RuntimeError("503 The model is overloaded"), "transient"),
        (RuntimeError("Request timed out"), "transient"),
        (StatusError("slow down", 429), "quota"),
        (RuntimeError("RESOURCE_EXHAUSTED: quota exceeded"), "quota"),
        (RuntimeError("Rate limit reached for requests"), "quota"),
        (StatusError("nope", 401), "auth"),
        (StatusError("nope", 403), "auth"),
        (RuntimeError("API key not valid"), "auth"),
        (StatusError("gone", 404), "model"),
        (OutputParserException("bad json"), "bad_output"),
        (RuntimeError("something odd"), "other"),
    ],
)
def test_classification(error: Exception, kind: str) -> None:
    assert classify_llm_error(error) == kind


# ------------------------------------------------------------------ asking


async def test_a_structured_answer_is_returned_and_messages_are_system_then_human() -> (
    None
):
    model = FakeModel(Answer(word="ok"))
    llm, _ = client(model)
    answer = await llm.ask("be brief", "say ok", Answer)
    assert answer == Answer(word="ok")
    assert model.messages == [[("system", "be brief"), ("human", "say ok")]]
    assert model.schemas == [Answer]


async def test_a_dict_answer_is_validated_into_the_schema() -> None:
    llm, _ = client(FakeModel({"word": "ok"}))
    assert await llm.ask("s", "h", Answer) == Answer(word="ok")


async def test_an_answer_that_does_not_fit_the_schema_is_a_bad_output_error() -> None:
    llm, _ = client(FakeModel({"unexpected": 1}))
    with pytest.raises(LlmError) as info:
        await llm.ask("s", "h", Answer)
    assert info.value.kind == "bad_output"


async def test_no_answer_at_all_is_a_bad_output_error() -> None:
    llm, _ = client(FakeModel(None))
    with pytest.raises(LlmError) as info:
        await llm.ask("s", "h", Answer)
    assert info.value.kind == "bad_output"


async def test_the_model_is_built_on_first_use_not_at_construction() -> None:
    built: list[int] = []

    def factory() -> FakeModel:
        built.append(1)
        return FakeModel(Answer(word="ok"), Answer(word="ok"))

    llm = LlmClient(factory)  # type: ignore[arg-type]
    assert built == []  # creating the client needs no API key
    await llm.ask("s", "h", Answer)
    await llm.ask("s", "h", Answer)  # second call reuses the model
    assert built == [1]


# ------------------------------------------------------------------ retry policy


async def test_a_timeout_is_retried_with_backoff_then_succeeds() -> None:
    llm, sleeps = client(FakeModel(TimeoutError(), TimeoutError(), Answer(word="ok")))
    assert await llm.ask("s", "h", Answer) == Answer(word="ok")
    assert sleeps == [1.0, 2.0]


async def test_retries_stop_after_three_and_the_message_is_plain() -> None:
    model = FakeModel(*[StatusError("busy", 503) for _ in range(10)])
    llm, sleeps = client(model)
    with pytest.raises(LlmError) as info:
        await llm.ask("s", "h", Answer)
    assert info.value.kind == "transient"
    assert len(model.messages) == 4 and sleeps == [1.0, 2.0, 3.0]
    assert "busy or not responding" in str(info.value)


async def test_a_quota_error_is_not_retried() -> None:
    model = FakeModel(StatusError("quota", 429), Answer(word="ok"))
    llm, sleeps = client(model)
    with pytest.raises(LlmError) as info:
        await llm.ask("s", "h", Answer)
    assert info.value.kind == "quota" and len(model.messages) == 1 and sleeps == []
    assert "wait a minute" in str(info.value)


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (StatusError("no", 401), "auth"),
        (StatusError("gone", 404), "model"),
        (OutputParserException("bad"), "bad_output"),
    ],
)
async def test_other_failures_fail_at_once(error: Exception, kind: str) -> None:
    model = FakeModel(error)
    llm, sleeps = client(model)
    with pytest.raises(LlmError) as info:
        await llm.ask("s", "h", Answer)
    assert info.value.kind == kind and len(model.messages) == 1 and sleeps == []


# ------------------------------------------------------------------ privacy


async def test_error_messages_never_contain_the_key_or_the_users_text() -> None:
    secret = "gsk_SECRET_KEY_123"
    user_text = "confidential launch plan"
    llm, _ = client(
        FakeModel(RuntimeError(f"API key {secret} rejected for {user_text}"))
    )
    with pytest.raises(LlmError) as info:
        await llm.ask("s", user_text, Answer)
    text = str(info.value)
    assert secret not in text and user_text not in text
    assert info.value.__cause__ is None and info.value.__suppress_context__


async def test_an_unknown_error_names_only_its_type() -> None:
    llm, _ = client(FakeModel(KeyError("secret user text")))
    with pytest.raises(LlmError) as info:
        await llm.ask("s", "h", Answer)
    assert "KeyError" in str(info.value) and "secret user text" not in str(info.value)


class FakeBadRequest(Exception):
    status_code = 400


def test_a_malformed_tool_call_is_reported_as_too_complex() -> None:
    """Finding F5: Groq answers HTTP 400 tool_use_failed for a very large request."""
    error = FakeBadRequest("Failed to call a function. code: tool_use_failed")
    assert classify_llm_error(error) == "too_complex"
    assert "too long" in _USER_MESSAGES["too_complex"]
