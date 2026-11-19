"""The tracing layer itself: clipping, the safety wrapper, and the in-memory recorder."""

import asyncio
from contextlib import contextmanager
from typing import Any

import pytest
from pydantic import BaseModel

from app.observability.tracing import (
    InMemoryTracer,
    NoopTracer,
    SafeTracer,
    clip,
    error_text,
    guarded,
)
from app.tools.base import FatalToolError

# ------------------------------------------------------------------ clip


def test_short_values_pass_through_unchanged() -> None:
    assert clip({"a": 1, "b": [True, None, "x"]}) == {"a": 1, "b": [True, None, "x"]}


def test_a_long_string_is_cut_and_says_by_how_much() -> None:
    clipped = clip("x" * 25, limit=10)
    assert clipped == "x" * 10 + "... [truncated 15 characters]"


def test_clipping_reaches_into_nested_values() -> None:
    clipped = clip({"outer": {"inner": ["y" * 30]}}, limit=5)
    assert clipped["outer"]["inner"][0].startswith("yyyyy... [truncated")


def test_models_become_plain_json() -> None:
    class Item(BaseModel):
        name: str

    assert clip(Item(name="n")) == {"name": "n"}


def test_long_lists_and_deep_nesting_are_bounded() -> None:
    assert len(clip(list(range(500)))) == 50
    deep: Any = "end"
    for _ in range(10):
        deep = {"k": deep}
    assert isinstance(clip(deep), dict)  # no recursion error, depth is capped


def test_unknown_objects_become_text() -> None:
    assert clip(object()).startswith("<object object")


# ------------------------------------------------------------------ error text


def test_a_tool_errors_message_is_kept_but_a_bugs_message_is_not() -> None:
    assert error_text(FatalToolError("no access")) == "no access"
    assert error_text(RuntimeError("secret user text")) == "RuntimeError"


def test_a_long_tool_error_is_clipped() -> None:
    assert len(error_text(FatalToolError("e" * 2000))) < 600


# ------------------------------------------------------------------ the recorder


def test_spans_nest_by_context() -> None:
    tracer = InMemoryTracer()
    with tracer.run("root", run_id="r", input="in", tags=["t"], metadata={"m": 1}):
        with tracer.span("a"), tracer.span("a1"):
            pass
        with tracer.span("b"):
            pass
    assert [root.shape() for root in tracer.roots] == [
        ("root", [("a", [("a1", [])]), ("b", [])])
    ]
    assert tracer.trace == {
        "run_id": "r",
        "input": "in",
        "tags": ["t"],
        "metadata": {"m": 1},
    }


def test_spans_record_their_details() -> None:
    tracer = InMemoryTracer()
    with tracer.span("llm", as_type="generation", input="p", model="m") as span:
        span.update(output="o", usage={"input": 1, "output": 2, "total": 3})
        span.update(level="ERROR", status_message="bad", metadata={"k": "v"})
    record = tracer.roots[0]
    assert (record.type, record.input, record.model) == ("generation", "p", "m")
    assert record.output == "o" and record.usage == {
        "input": 1,
        "output": 2,
        "total": 3,
    }
    assert (record.level, record.status_message, record.metadata) == (
        "ERROR",
        "bad",
        {"k": "v"},
    )


async def test_nesting_survives_concurrent_tasks() -> None:
    """Each task keeps its own current span, the way OpenTelemetry context does."""
    tracer = InMemoryTracer()

    async def worker(name: str) -> None:
        with tracer.span(name):
            await asyncio.sleep(0)
            with tracer.span(f"{name}-child"):
                await asyncio.sleep(0)

    with tracer.run("root", run_id="r", input=None, tags=[], metadata={}):
        await asyncio.gather(worker("one"), worker("two"))
    shape = tracer.roots[0].shape()
    assert sorted(shape[1]) == [
        ("one", [("one-child", [])]),
        ("two", [("two-child", [])]),
    ]


def test_find_returns_every_span_with_a_name() -> None:
    tracer = InMemoryTracer()
    with tracer.span("root"):
        with tracer.span("x"):
            pass
        with tracer.span("x"):
            pass
    assert len(tracer.roots[0].find("x")) == 2


def test_scores_and_flushes_are_counted() -> None:
    tracer = InMemoryTracer()
    tracer.score("run_status", "completed")
    tracer.flush()
    assert tracer.scores == [("run_status", "completed")] and tracer.flushes == 1


# ------------------------------------------------------------------ the safety wrapper


class ExplodingTracer:
    """Fails at every point, to prove none of it can reach the caller."""

    def run(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("backend down")

    def span(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("backend down")

    def score(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("backend down")

    def flush(self) -> None:
        raise RuntimeError("backend down")


class BrokenOnUse:
    """Starts fine, then fails when updated and when closed."""

    @contextmanager
    def span(self, *args: Any, **kwargs: Any) -> Any:
        class Bad:
            def update(self, **_: Any) -> None:
                raise RuntimeError("update failed")

        yield Bad()
        raise RuntimeError("close failed")


def test_a_backend_that_fails_everywhere_changes_nothing_for_the_caller() -> None:
    tracer = SafeTracer(ExplodingTracer())  # type: ignore[arg-type]
    with tracer.run("r", run_id="x", input=None, tags=[], metadata={}) as root:
        root.update(output="o")
        with tracer.span("s") as span:
            span.update(output="o")
    tracer.score("a", 1.0)
    tracer.flush()


def test_a_failure_while_updating_or_closing_is_ignored() -> None:
    tracer = SafeTracer(BrokenOnUse())  # type: ignore[arg-type]
    with tracer.span("s") as span:
        span.update(output="o")


def test_the_callers_own_exception_is_never_swallowed() -> None:
    tracer = SafeTracer(InMemoryTracer())
    with pytest.raises(ValueError, match="mine"), tracer.span("s"):
        raise ValueError("mine")
    broken = SafeTracer(ExplodingTracer())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="mine"), broken.span("s"):
        raise ValueError("mine")


def test_only_the_first_failure_is_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tracer = SafeTracer(ExplodingTracer())  # type: ignore[arg-type]
    caplog.set_level("DEBUG")
    for _ in range(3):
        tracer.score("a", 1.0)
    levels = [r.levelname for r in caplog.records if "Tracing error" in r.message]
    assert levels == ["WARNING", "DEBUG", "DEBUG"]
    assert "backend down" not in caplog.text  # the type is logged, never the message


def test_guarding_twice_does_not_nest_wrappers() -> None:
    once = guarded(NoopTracer())
    assert guarded(once) is once


def test_the_noop_tracer_does_nothing_and_never_fails() -> None:
    tracer = NoopTracer()
    with tracer.run("r", run_id="x", input=None, tags=[], metadata={}) as root:
        root.update(output="o", level="ERROR")
        with tracer.span("s", as_type="tool", input={"a": 1}) as span:
            span.update(usage={"total": 1})
    tracer.score("a", "b")
    tracer.flush()


def test_shutdown_is_counted_ignored_by_the_noop_and_made_safe() -> None:
    memory = InMemoryTracer()
    memory.shutdown()
    assert memory.shutdowns == 1
    NoopTracer().shutdown()
    SafeTracer(ExplodingTracer()).shutdown()  # type: ignore[arg-type]


def test_trace_url_is_none_for_the_noop_and_safe_when_the_backend_fails() -> None:
    class Failing(ExplodingTracer):
        def trace_url(self, run_id: str) -> str:
            raise RuntimeError("backend down")

    assert NoopTracer().trace_url("r") is None
    assert SafeTracer(Failing()).trace_url("r") is None  # type: ignore[arg-type]
    memory = InMemoryTracer()
    assert memory.trace_url("r") is None
    with memory.run("root", run_id="r", input=None, tags=[], metadata={}):
        pass
    assert memory.trace_url("r") == "memory://trace/r"
