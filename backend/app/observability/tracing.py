"""The tracing layer: how the agent records what it does, independent of any backend.

The agent talks only to the small `Tracer` interface below. `LangfuseTracer` (Task 3.2) sends the
spans to Langfuse; `NoopTracer` (the default) does nothing; `InMemoryTracer` records the span tree
so tests can assert its exact shape without a network.

Why our own spans instead of LangChain's Langfuse callback handler: the agent's tool calls
(Notion, Drive, Slack) go through our adapters, not LangChain, so a handler would never see them;
and explicit spans behave the same on every supported Python version and can be tested offline.

The one rule that matters most: TRACING MUST NEVER BREAK A RUN. `guarded()` wraps any tracer so
an error inside the backend is logged and ignored, and a user's exception is never swallowed.
"""

import logging
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from app.tools.base import ToolError

logger = logging.getLogger(__name__)

SpanType = Literal["span", "generation", "tool"]

# Long values are clipped before they reach a trace: it keeps traces light and limits how much
# user text leaves the machine. Prompts get a larger limit because they are what you debug.
TOOL_LIMIT = 2000
LLM_LIMIT = 8000
MAX_DEPTH = 4
MAX_ITEMS = 50


def clip(value: Any, limit: int = TOOL_LIMIT, _depth: int = 0) -> Any:
    """A JSON-friendly, size-bounded copy of `value` for a trace."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return f"{value[:limit]}... [truncated {len(value) - limit} characters]"
    if _depth < MAX_DEPTH:
        if isinstance(value, Mapping):
            return {str(k): clip(v, limit, _depth + 1) for k, v in value.items()}
        if isinstance(value, list | tuple | set | frozenset):
            return [clip(v, limit, _depth + 1) for v in list(value)[:MAX_ITEMS]]
    return clip(str(value), limit, _depth)


def error_text(error: BaseException) -> str:
    """What to record on a failed span: a tool error's message is already redacted by the tool
    layer; anything else is a bug whose message could hold user text, so only its type is kept.
    """
    return (
        clip(str(error), 500) if isinstance(error, ToolError) else type(error).__name__
    )


# --------------------------------------------------------------------------- interface


class Span(Protocol):
    def update(
        self,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str | None = None,  # "WARNING" or "ERROR"; unset means normal
        status_message: str | None = None,
        usage: dict[str, int] | None = None,  # {"input": n, "output": n, "total": n}
    ) -> None: ...


class Tracer(Protocol):
    def run(
        self,
        name: str,
        *,
        run_id: str,
        input: Any,
        tags: list[str],
        metadata: dict[str, Any],
    ) -> AbstractContextManager[Span]:
        """The root of one run's trace. Everything started inside it nests under it."""
        ...

    def span(
        self,
        name: str,
        *,
        as_type: SpanType = "span",
        input: Any = None,
        metadata: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> AbstractContextManager[Span]: ...

    def score(
        self, name: str, value: float | str, *, comment: str | None = None
    ) -> None:
        """A result attached to the current run's trace (a number or a category)."""
        ...

    def flush(self) -> None:
        """Send whatever is still buffered (a run's trace is exported in the background)."""
        ...

    def shutdown(self) -> None:
        """Flush and stop the exporter; call once when the process ends."""
        ...

    def trace_url(self, run_id: str) -> str | None:
        """The link to a finished run's trace, if the backend has one (None otherwise)."""
        ...


# --------------------------------------------------------------------------- no-op


class _NoopSpan:
    def update(self, **_: Any) -> None:
        return None


class NoopTracer:
    """Records nothing. The default, so tracing is opt-in and tests need no setup."""

    def run(self, name: str, **_: Any) -> AbstractContextManager[Span]:
        return nullcontext(_NoopSpan())

    def span(self, name: str, **_: Any) -> AbstractContextManager[Span]:
        return nullcontext(_NoopSpan())

    def score(self, name: str, value: float | str, **_: Any) -> None:
        return None

    def flush(self) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def trace_url(self, run_id: str) -> str | None:
        return None


# --------------------------------------------------------------------------- safety


class _SafeSpan:
    def __init__(self, inner: Span | None, report: Callable[[Exception], None]) -> None:
        self._inner = inner
        self._report = report

    def update(self, **kwargs: Any) -> None:
        if self._inner is None:
            return
        try:
            self._inner.update(**kwargs)
        except Exception as error:  # noqa: BLE001 - tracing must never break a run
            self._report(error)


class _SafeContext:
    def __init__(
        self,
        make: Callable[[], AbstractContextManager[Span]],
        report: Callable[[Exception], None],
    ) -> None:
        self._make = make
        self._report = report
        self._inner: AbstractContextManager[Span] | None = None

    def __enter__(self) -> Span:
        try:
            self._inner = self._make()
            return _SafeSpan(self._inner.__enter__(), self._report)
        except Exception as error:  # noqa: BLE001
            self._inner = None
            self._report(error)
            return _SafeSpan(None, self._report)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        if self._inner is not None:
            try:
                self._inner.__exit__(exc_type, exc, tb)
            except Exception as error:  # noqa: BLE001
                self._report(error)
        return False  # never swallow the caller's own exception


class SafeTracer:
    """Wraps a tracer so a failing backend costs a log line, never a failed workflow."""

    def __init__(self, inner: Tracer) -> None:
        self._inner = inner
        self._warned = False

    def _report(self, error: Exception) -> None:
        # Type only (a message could hold user text); the first failure is a warning, the
        # rest are quiet, so an outage does not flood the log.
        level = logging.WARNING if not self._warned else logging.DEBUG
        self._warned = True
        logger.log(level, "Tracing error ignored: %s", type(error).__name__)

    def run(self, name: str, **kwargs: Any) -> AbstractContextManager[Span]:
        return _SafeContext(lambda: self._inner.run(name, **kwargs), self._report)

    def span(self, name: str, **kwargs: Any) -> AbstractContextManager[Span]:
        return _SafeContext(lambda: self._inner.span(name, **kwargs), self._report)

    def score(self, name: str, value: float | str, **kwargs: Any) -> None:
        try:
            self._inner.score(name, value, **kwargs)
        except Exception as error:  # noqa: BLE001
            self._report(error)

    def flush(self) -> None:
        try:
            self._inner.flush()
        except Exception as error:  # noqa: BLE001
            self._report(error)

    def shutdown(self) -> None:
        try:
            self._inner.shutdown()
        except Exception as error:  # noqa: BLE001
            self._report(error)

    def trace_url(self, run_id: str) -> str | None:
        try:
            return self._inner.trace_url(run_id)
        except Exception as error:  # noqa: BLE001
            self._report(error)
            return None


def guarded(tracer: Tracer) -> Tracer:
    """`tracer`, made safe (wrapping twice is harmless)."""
    return tracer if isinstance(tracer, SafeTracer) else SafeTracer(tracer)


# --------------------------------------------------------------------------- in memory


@dataclass
class SpanRecord:
    """One recorded span (for tests). Implements the `Span` interface."""

    name: str
    type: str
    input: Any = None
    model: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    output: Any = None
    level: str | None = None
    status_message: str | None = None
    usage: dict[str, int] | None = None
    children: list["SpanRecord"] = field(default_factory=list)

    def update(
        self,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str | None = None,
        status_message: str | None = None,
        usage: dict[str, int] | None = None,
    ) -> None:
        if output is not None:
            self.output = output
        if metadata:
            self.metadata.update(metadata)
        if level is not None:
            self.level = level
        if status_message is not None:
            self.status_message = status_message
        if usage is not None:
            self.usage = usage

    def find(self, name: str) -> list["SpanRecord"]:
        """Every span called `name` in this subtree (including this one)."""
        found = [self] if self.name == name else []
        for child in self.children:
            found.extend(child.find(name))
        return found

    def shape(self) -> Any:
        """The tree as nested (name, [children]) tuples: easy to assert on."""
        return (self.name, [child.shape() for child in self.children])


_current: ContextVar[SpanRecord | None] = ContextVar("current_span", default=None)


class InMemoryTracer:
    """Records the span tree. Nesting uses a context variable, the same mechanism OpenTelemetry
    (and so Langfuse) uses, so tests exercise the real propagation through LangGraph nodes.
    """

    def __init__(self) -> None:
        self.roots: list[SpanRecord] = []
        self.scores: list[tuple[str, float | str]] = []
        self.trace: dict[str, Any] = {}
        self.flushes = 0
        self.shutdowns = 0

    @contextmanager
    def _record(self, record: SpanRecord) -> Iterator[SpanRecord]:
        parent = _current.get()
        (parent.children if parent else self.roots).append(record)
        token = _current.set(record)
        try:
            yield record
        finally:
            _current.reset(token)

    def run(
        self,
        name: str,
        *,
        run_id: str,
        input: Any,
        tags: list[str],
        metadata: dict[str, Any],
    ) -> AbstractContextManager[Span]:
        self.trace = {
            "run_id": run_id,
            "input": input,
            "tags": list(tags),
            "metadata": dict(metadata),
        }
        return self._record(SpanRecord(name=name, type="trace", input=input))

    def span(
        self,
        name: str,
        *,
        as_type: SpanType = "span",
        input: Any = None,
        metadata: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> AbstractContextManager[Span]:
        return self._record(
            SpanRecord(
                name=name,
                type=as_type,
                input=input,
                model=model,
                metadata=dict(metadata or {}),
            )
        )

    def score(
        self, name: str, value: float | str, *, comment: str | None = None
    ) -> None:
        self.scores.append((name, value))

    def flush(self) -> None:
        self.flushes += 1

    def shutdown(self) -> None:
        self.shutdowns += 1

    def trace_url(self, run_id: str) -> str | None:
        # A stand-in link so tests can see that the run's link reaches the store.
        return (
            f"memory://trace/{run_id}" if self.trace.get("run_id") == run_id else None
        )
