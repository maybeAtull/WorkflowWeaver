"""The Langfuse backend for the tracing interface (see tracing.py for the design).

Mapping onto Langfuse 5.2.0:
  - a run is a root span inside `propagate_attributes`, which sets the trace name, session id
    (the run id), tags and version on the root and every span under it;
  - our spans become observations of type span / tool / generation, and our `usage` becomes
    Langfuse's `usage_details`;
  - a text score becomes a categorical score, a number a numeric one.

Langfuse limits what `propagate_attributes` accepts (ASCII session id, metadata keys and trace
name; values of at most 200 characters once JSON-encoded), so everything passes through the
small sanitizers below instead of failing a run over a long header.

Always used through `guarded()` (the deps do that), so a Langfuse outage cannot fail a workflow.
"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from langfuse import Langfuse, propagate_attributes

from app.config import Settings
from app.observability.tracing import Span, SpanType

# Langfuse allows 200 characters AFTER JSON-encoding; the quotes add two, so stay well under.
MAX_ATTRIBUTE_CHARS = 180
MAX_TAG_CHARS = 200
CLIENT_TIMEOUT_SECONDS = 20


def _ascii(text: str) -> str:
    return text.encode("ascii", "replace").decode("ascii")


def trace_metadata(metadata: Mapping[str, Any]) -> dict[str, str]:
    """ASCII keys and short string values, as `propagate_attributes` requires."""
    return {
        _ascii(str(key)): _ascii(str(value))[:MAX_ATTRIBUTE_CHARS]
        for key, value in metadata.items()
    }


class _LangfuseSpan:
    """Adapts a Langfuse observation to our `Span`: only fields that were set are sent."""

    def __init__(self, observation: Any) -> None:
        self._observation = observation

    def update(
        self,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str | None = None,
        status_message: str | None = None,
        usage: dict[str, int] | None = None,
    ) -> None:
        fields = {
            "output": output,
            "metadata": metadata,
            "level": level,
            "status_message": status_message,
            "usage_details": usage,
        }
        self._observation.update(**{k: v for k, v in fields.items() if v is not None})


class LangfuseTracer:
    def __init__(self, client: Langfuse) -> None:
        self._client = client
        self._last_trace_id: str | None = None

    @property
    def client(self) -> Langfuse:
        """The underlying client (the live trace check reads traces back through it)."""
        return self._client

    @contextmanager
    def run(
        self,
        name: str,
        *,
        run_id: str,
        input: Any,
        tags: list[str],
        metadata: dict[str, Any],
    ) -> Iterator[Span]:
        version = metadata.get("prompt_version")
        with propagate_attributes(
            trace_name=_ascii(name),
            session_id=_ascii(run_id)[:200],
            tags=[_ascii(tag)[:MAX_TAG_CHARS] for tag in tags],
            metadata=trace_metadata(metadata),
            version=str(version) if version is not None else None,
        ), self._client.start_as_current_observation(
            name=name, as_type="span", input=input
        ) as root:
            self._last_trace_id = self._client.get_current_trace_id()
            yield _LangfuseSpan(root)

    @contextmanager
    def span(
        self,
        name: str,
        *,
        as_type: SpanType = "span",
        input: Any = None,
        metadata: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> Iterator[Span]:
        arguments: dict[str, Any] = {"name": name, "as_type": as_type, "input": input}
        if metadata:
            arguments["metadata"] = metadata
        if model and as_type == "generation":
            arguments["model"] = model
        with self._client.start_as_current_observation(**arguments) as observation:
            yield _LangfuseSpan(observation)

    def score(
        self, name: str, value: float | str, *, comment: str | None = None
    ) -> None:
        self._client.score_current_trace(
            name=name,
            value=value,
            data_type="CATEGORICAL" if isinstance(value, str) else "NUMERIC",
            comment=comment,
        )

    def flush(self) -> None:
        self._client.flush()

    def shutdown(self) -> None:
        self._client.shutdown()

    @property
    def last_trace_id(self) -> str | None:
        return self._last_trace_id

    def last_trace_url(self) -> str | None:
        """The Langfuse link of the most recent run (for the runner to print)."""
        if self._last_trace_id is None:
            return None
        return self._client.get_trace_url(trace_id=self._last_trace_id)


def create_tracer(settings: Settings) -> LangfuseTracer:
    """A Langfuse tracer from the settings. Building it makes no network call."""
    client = Langfuse(
        public_key=settings.langfuse_public_key.get_secret_value(),
        secret_key=settings.langfuse_secret_key.get_secret_value(),
        base_url=settings.langfuse_host,
        timeout=CLIENT_TIMEOUT_SECONDS,
    )
    return LangfuseTracer(client)
