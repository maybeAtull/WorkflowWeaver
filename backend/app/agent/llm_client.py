"""One way for agent nodes to ask the LLM for a structured answer, and to fail politely.

`LlmClient.ask(system, human, schema)` returns a validated Pydantic object or raises `LlmError`
with a message that is safe to show the user: never the API key, never the user's own text,
only a classification of what went wrong.

Retry policy (the project's retry layer, on top of the provider library's two quick retries):
  - timeouts and overload (503 and friends) are retried with backoff, up to MAX_RETRIES;
  - quota / rate-limit (429) is NOT retried: a per-minute limit does not clear in a few
    seconds, and retrying only burns more of the free tier;
  - bad key, unknown model or an unusable answer fail at once.
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypeVar

from langchain_core.callbacks import get_usage_metadata_callback
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, ValidationError

from app.agent.retry import MAX_RETRIES, backoff_delay
from app.llm import get_llm
from app.observability.tracing import LLM_LIMIT, NoopTracer, Tracer, clip, guarded

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

LlmErrorKind = Literal[
    "transient", "quota", "auth", "model", "bad_output", "too_complex", "other"
]

# How a provider words "the model's tool call was malformed" (Groq: HTTP 400 tool_use_failed).
# Seen with very large requests; repeating the same call would fail the same way.
_MALFORMED_CALL_WORDS = ("tool_use_failed", "failed to call a function")

_TRANSIENT_STATUS = {500, 502, 503, 504, 529}
_STATUS_IN_TEXT = re.compile(r"\b([45]\d\d)\b")

_USER_MESSAGES: dict[str, str] = {
    "transient": "The AI service is busy or not responding. Please try again shortly.",
    "quota": "The AI service has reached its usage limit. Please wait a minute and try again.",
    "auth": "The AI service rejected the configured key. Check LLM_API_KEY and LLM_PROVIDER.",
    "model": "The configured AI model is not available. Check LLM_MODEL.",
    "bad_output": "The AI answered in an unexpected format. Please try again.",
    "too_complex": (
        "That request was too long or detailed for the AI to plan reliably. "
        "Try fewer items, or split it into two workflows."
    ),
}


class LlmError(Exception):
    """An LLM call failed. `str(error)` is safe to show the user."""

    def __init__(self, message: str, kind: LlmErrorKind) -> None:
        super().__init__(message)
        self.kind = kind


def _status_code(exc: BaseException) -> int | None:
    """The HTTP status the provider library attached, if any (attribute names differ)."""
    for holder in (exc, getattr(exc, "response", None)):
        for name in ("status_code", "code", "http_status"):
            value = getattr(holder, name, None)
            if isinstance(value, int):
                return value
    return None


def classify_llm_error(exc: BaseException) -> LlmErrorKind:
    if isinstance(exc, OutputParserException | ValidationError):
        return "bad_output"
    if isinstance(exc, TimeoutError | asyncio.TimeoutError):  # same class on 3.11+
        return "transient"

    text = f"{type(exc).__name__} {exc}".lower()
    status = _status_code(exc)
    if status is None:
        found = _STATUS_IN_TEXT.search(text)
        status = int(found.group(1)) if found else None

    if status == 400 and any(w in text for w in _MALFORMED_CALL_WORDS):
        return "too_complex"
    # The status decides first (a 503 message can mention other words); text is the fallback.
    if status == 429 or any(
        w in text for w in ("resource_exhausted", "rate limit", "quota", "too many")
    ):
        return "quota"
    if status in _TRANSIENT_STATUS or any(
        w in text for w in ("unavailable", "overloaded", "timeout", "timed out")
    ):
        return "transient"
    if status in (401, 403) or any(
        w in text for w in ("api key", "unauthorized", "permission denied")
    ):
        return "auth"
    if status == 404 or "not_found" in text or "model not found" in text:
        return "model"
    return "other"


def usage_from_metadata(metadata: dict[str, Any]) -> dict[str, int] | None:
    """Token counts for a span, summed over the models used (normally one). None when the
    provider reported nothing: no usage is better than an invented one."""
    totals = {"input": 0, "output": 0, "total": 0}
    for usage in metadata.values():
        totals["input"] += int(usage.get("input_tokens", 0))
        totals["output"] += int(usage.get("output_tokens", 0))
        totals["total"] += int(usage.get("total_tokens", 0))
    return totals if totals["total"] or totals["input"] or totals["output"] else None


class LlmClient:
    def __init__(
        self,
        model_factory: Callable[[], BaseChatModel] = get_llm,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        backoff: Callable[[int], float] = backoff_delay,
        tracer: Tracer | None = None,
    ) -> None:
        # Built on first use, so creating the client (or the agent's deps) needs no API key.
        self._model_factory = model_factory
        self._model: BaseChatModel | None = None
        self._sleep = sleep
        self._backoff = backoff
        self._tracer = guarded(tracer or NoopTracer())

    def _structured(self, schema: type[T]) -> Any:
        if self._model is None:
            self._model = self._model_factory()
        return self._model.with_structured_output(schema)

    def _model_name(self) -> str | None:
        return getattr(self._model, "model_name", None) or getattr(
            self._model, "model", None
        )

    async def ask(
        self, system: str, human: str, schema: type[T], name: str = "llm"
    ) -> T:
        """Ask for a structured answer. `name` labels the call in traces (llm:<name>)."""
        retries = 0
        while True:
            try:
                return await self._attempt(system, human, schema, name, retries + 1)
            except Exception as error:  # noqa: BLE001
                # Provider libraries raise many exception types; all are classified below.
                kind = classify_llm_error(error)
                # Log the classification and type only: the message may hold the key or user text.
                logger.warning("LLM call failed: %s (%s)", kind, type(error).__name__)
                if kind == "transient" and retries < MAX_RETRIES:
                    retries += 1
                    await self._sleep(self._backoff(retries))
                    continue
                message = _USER_MESSAGES.get(
                    kind,
                    f"The AI service failed unexpectedly ({type(error).__name__}).",
                )
                raise LlmError(message, kind) from None

    async def _attempt(
        self, system: str, human: str, schema: type[T], name: str, attempt: int
    ) -> T:
        """One call to the model, recorded as a generation span (so every retry shows)."""
        structured = self._structured(schema)
        with self._tracer.span(
            f"llm:{name}",
            as_type="generation",
            input=clip(
                [
                    {"role": "system", "content": system},
                    {"role": "human", "content": human},
                ],
                LLM_LIMIT,
            ),
            metadata={"attempt": attempt},
            model=self._model_name(),
        ) as span:
            try:
                # Collects the token usage the provider reports for calls made inside it.
                with get_usage_metadata_callback() as usage_callback:
                    answer = await structured.ainvoke(
                        [("system", system), ("human", human)]
                    )
                result = self._as_schema(answer, schema)
            except Exception as error:
                # The classification only: the message may hold the key or the user's text.
                span.update(level="ERROR", status_message=classify_llm_error(error))
                raise
            span.update(
                output=clip(result, LLM_LIMIT),
                usage=usage_from_metadata(usage_callback.usage_metadata),
            )
            return result

    @staticmethod
    def _as_schema(answer: object, schema: type[T]) -> T:
        if isinstance(answer, schema):
            return answer
        if isinstance(answer, dict):  # some providers return the parsed dict
            return schema.model_validate(answer)
        raise OutputParserException("The model returned no structured answer")
