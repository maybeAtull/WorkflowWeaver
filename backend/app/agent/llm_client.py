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

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, ValidationError

from app.agent.retry import MAX_RETRIES, backoff_delay
from app.llm import get_llm

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

LlmErrorKind = Literal["transient", "quota", "auth", "model", "bad_output", "other"]

_TRANSIENT_STATUS = {500, 502, 503, 504, 529}
_STATUS_IN_TEXT = re.compile(r"\b([45]\d\d)\b")

_USER_MESSAGES: dict[str, str] = {
    "transient": "The AI service is busy or not responding. Please try again shortly.",
    "quota": "The AI service has reached its usage limit. Please wait a minute and try again.",
    "auth": "The AI service rejected the configured key. Check LLM_API_KEY and LLM_PROVIDER.",
    "model": "The configured AI model is not available. Check LLM_MODEL.",
    "bad_output": "The AI answered in an unexpected format. Please try again.",
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


class LlmClient:
    def __init__(
        self,
        model_factory: Callable[[], BaseChatModel] = get_llm,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        backoff: Callable[[int], float] = backoff_delay,
    ) -> None:
        # Built on first use, so creating the client (or the agent's deps) needs no API key.
        self._model_factory = model_factory
        self._model: BaseChatModel | None = None
        self._sleep = sleep
        self._backoff = backoff

    def _structured(self, schema: type[T]) -> Any:
        if self._model is None:
            self._model = self._model_factory()
        return self._model.with_structured_output(schema)

    async def ask(self, system: str, human: str, schema: type[T]) -> T:
        retries = 0
        while True:
            try:
                answer = await self._structured(schema).ainvoke(
                    [("system", system), ("human", human)]
                )
                return self._as_schema(answer, schema)
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

    @staticmethod
    def _as_schema(answer: object, schema: type[T]) -> T:
        if isinstance(answer, schema):
            return answer
        if isinstance(answer, dict):  # some providers return the parsed dict
            return schema.model_validate(answer)
        raise OutputParserException("The model returned no structured answer")
