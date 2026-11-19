"""The Langfuse backend against a fake Langfuse client: what it sends, with no network."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from app.config import Settings
from app.observability import langfuse_tracer
from app.observability.langfuse_tracer import (
    MAX_ATTRIBUTE_CHARS,
    LangfuseTracer,
    create_tracer,
    trace_metadata,
)
from tests.test_config import FAKE_ENV


class FakeObservation:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    def update(self, **fields: Any) -> None:
        self.updates.append(fields)


class FakeClient:
    """Records what the tracer asks Langfuse to do."""

    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.observations: list[FakeObservation] = []
        self.scores: list[dict[str, Any]] = []
        self.flushes = 0
        self.shutdowns = 0

    @contextmanager
    def start_as_current_observation(self, **kwargs: Any) -> Iterator[FakeObservation]:
        self.started.append(kwargs)
        observation = FakeObservation()
        self.observations.append(observation)
        yield observation

    def get_current_trace_id(self) -> str:
        return "trace-1"

    def get_trace_url(self, *, trace_id: str) -> str:
        return f"https://langfuse.test/trace/{trace_id}"

    def score_current_trace(self, **kwargs: Any) -> None:
        self.scores.append(kwargs)

    def flush(self) -> None:
        self.flushes += 1

    def shutdown(self) -> None:
        self.shutdowns += 1


@pytest.fixture
def propagated(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replaces propagate_attributes with a recorder (it needs a live OpenTelemetry setup)."""
    calls: list[dict[str, Any]] = []

    @contextmanager
    def fake(**kwargs: Any) -> Iterator[None]:
        calls.append(kwargs)
        yield

    monkeypatch.setattr(langfuse_tracer, "propagate_attributes", fake)
    return calls


def make() -> tuple[LangfuseTracer, FakeClient]:
    client = FakeClient()
    return LangfuseTracer(client), client  # type: ignore[arg-type]


# ------------------------------------------------------------------ the run


def test_a_run_sets_the_trace_attributes_and_opens_a_root_span(
    propagated: list[dict[str, Any]],
) -> None:
    tracer, client = make()
    with tracer.run(
        "workflow-run",
        run_id="abc123",
        input="the request",
        tags=["workflowweaver", "run:abc123"],
        metadata={"run_id": "abc123", "prompt_version": "2", "llm_model": "m"},
    ):
        pass
    assert propagated == [
        {
            "trace_name": "workflow-run",
            "session_id": "abc123",
            "tags": ["workflowweaver", "run:abc123"],
            "metadata": {"run_id": "abc123", "prompt_version": "2", "llm_model": "m"},
            "version": "2",
        }
    ]
    assert client.started == [
        {"name": "workflow-run", "as_type": "span", "input": "the request"}
    ]


def test_the_version_is_left_out_when_there_is_no_prompt_version(
    propagated: list[dict[str, Any]],
) -> None:
    tracer, _ = make()
    with tracer.run("r", run_id="x", input=None, tags=[], metadata={}):
        pass
    assert propagated[0]["version"] is None


def test_attributes_are_made_ascii_and_short_enough_for_langfuse() -> None:
    cleaned = trace_metadata({"kéy": "välue " + "x" * 500, 7: 3.5})
    assert list(cleaned) == ["k?y", "7"]
    assert cleaned["k?y"].startswith("v?lue ")
    assert all(len(v) <= MAX_ATTRIBUTE_CHARS for v in cleaned.values())
    assert cleaned["7"] == "3.5"


def test_the_trace_link_is_known_after_a_run(propagated: list[dict[str, Any]]) -> None:
    tracer, _ = make()
    assert tracer.last_trace_url() is None
    with tracer.run("r", run_id="x", input=None, tags=[], metadata={}):
        pass
    assert tracer.last_trace_url() == "https://langfuse.test/trace/trace-1"


# ------------------------------------------------------------------ spans


def test_a_tool_span_is_a_tool_observation_without_a_model() -> None:
    tracer, client = make()
    with tracer.span(
        "tool:notion.create_page",
        as_type="tool",
        input={"title": "t"},
        metadata={"attempt": 1},
        model="ignored",
    ):
        pass
    assert client.started == [
        {
            "name": "tool:notion.create_page",
            "as_type": "tool",
            "input": {"title": "t"},
            "metadata": {"attempt": 1},
        }
    ]


def test_a_generation_span_carries_its_model() -> None:
    tracer, client = make()
    with tracer.span("llm:plan", as_type="generation", input="p", model="m1"):
        pass
    assert client.started == [
        {"name": "llm:plan", "as_type": "generation", "input": "p", "model": "m1"}
    ]


def test_empty_metadata_is_not_sent() -> None:
    tracer, client = make()
    with tracer.span("node", metadata={}):
        pass
    assert "metadata" not in client.started[0]


def test_updates_send_only_what_was_set_and_map_usage() -> None:
    tracer, client = make()
    with tracer.span("llm:plan", as_type="generation") as span:
        span.update(output={"a": 1}, usage={"input": 1, "output": 2, "total": 3})
        span.update(level="ERROR", status_message="auth")
        span.update()
    assert client.observations[0].updates == [
        {"output": {"a": 1}, "usage_details": {"input": 1, "output": 2, "total": 3}},
        {"level": "ERROR", "status_message": "auth"},
        {},
    ]


def test_an_exception_in_a_span_still_propagates() -> None:
    tracer, _ = make()
    with pytest.raises(ValueError, match="mine"), tracer.span("s"):
        raise ValueError("mine")


# ------------------------------------------------------------------ scores, flush, create


def test_text_scores_are_categorical_and_numbers_are_numeric() -> None:
    tracer, client = make()
    tracer.score("run_status", "completed")
    tracer.score("retries", 2.0, comment="two retries")
    assert client.scores == [
        {
            "name": "run_status",
            "value": "completed",
            "data_type": "CATEGORICAL",
            "comment": None,
        },
        {
            "name": "retries",
            "value": 2.0,
            "data_type": "NUMERIC",
            "comment": "two retries",
        },
    ]


def test_flush_and_shutdown_pass_through() -> None:
    tracer, client = make()
    tracer.flush()
    tracer.shutdown()
    assert (client.flushes, client.shutdowns) == (1, 1)


def test_the_tracer_is_built_from_the_settings_without_any_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[dict[str, Any]] = []

    class RecordingLangfuse(FakeClient):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__()
            built.append(kwargs)

    monkeypatch.setattr(langfuse_tracer, "Langfuse", RecordingLangfuse)
    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("LANGFUSE_HOST", "https://eu.langfuse.test")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert isinstance(create_tracer(settings), LangfuseTracer)
    assert built == [
        {
            "public_key": FAKE_ENV["LANGFUSE_PUBLIC_KEY"],
            "secret_key": FAKE_ENV["LANGFUSE_SECRET_KEY"],
            "base_url": "https://eu.langfuse.test",
            "timeout": 20,
        }
    ]
