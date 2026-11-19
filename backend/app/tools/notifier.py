"""Notifier: post the final workflow summary to team chat (Slack or Microsoft Teams).

The planner sees ONE tool, "notifier", with one action, "send", and never needs to know which
chat tool is behind it. A provider-neutral NotificationMessage is rendered by each provider
into its own webhook format. A chat message cannot be unsent through a webhook, so the action
is not reversible and is always the last step of a workflow.
"""

from abc import abstractmethod
from typing import Any, ClassVar, Literal

import httpx
from pydantic import BaseModel, Field, field_validator

from app.config import Settings
from app.tools.base import (
    ActionSpec,
    FatalToolError,
    ToolAdapter,
    ToolContractError,
    ToolResult,
    TransientToolError,
)

REQUEST_TIMEOUT_SECONDS = 15
MAX_ERROR_CHARS = 300

Status = Literal["success", "warning", "failure"]


class Fact(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    value: str = Field(max_length=1000)


class Link(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=2000)

    @field_validator("url")
    @classmethod
    def _http_only(cls, url: str) -> str:
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("link must start with http:// or https://")
        return url


class NotificationMessage(BaseModel):
    """The provider-neutral message. Generous caps here; renderers clip to each provider's limits."""

    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(default="", max_length=5000)
    status: Status = "success"
    facts: list[Fact] = Field(default_factory=list, max_length=20)
    links: list[Link] = Field(default_factory=list, max_length=10)


def clip(text: str, limit: int) -> str:
    """Shorten to `limit` characters with an ellipsis. A notification must never fail on length."""
    return text if len(text) <= limit else text[: limit - 1] + "\N{HORIZONTAL ELLIPSIS}"


class WebhookNotifier(ToolAdapter):
    """Shared base: validate, render, POST to the webhook, map the reply to our error kinds."""

    name: ClassVar[str] = "notifier"
    actions: ClassVar[dict[str, ActionSpec]] = {
        "send": ActionSpec(
            name="send",
            description=(
                "Post a summary message to the team chat (Slack or Microsoft Teams). "
                "It cannot be undone, so it is always the last step."
            ),
            params_model=NotificationMessage,
            reversible=False,
            repeat_safe=True,  # a repeated send is at worst a duplicate message
            # The planner cannot know what will exist when the message goes out, so the
            # executor supplies these from the run's real results: links to the items that
            # were created (an LLM would invent URLs) and the status.
            planner_hidden_params=frozenset({"status", "facts", "links"}),
        )
    }

    provider: ClassVar[str]
    success_statuses: ClassVar[frozenset[int]] = frozenset({200})
    # False when the webhook only acknowledges the request (Teams can answer 202 without posting).
    delivery_confirmed: ClassVar[bool] = True

    def __init__(self, webhook_url: str, http: httpx.AsyncClient | None = None) -> None:
        self._url = webhook_url
        self._http = http  # injected in tests; otherwise a short-lived client per send

    @abstractmethod
    def render(self, message: NotificationMessage) -> dict[str, Any]:
        """Turn the neutral message into this provider's webhook JSON."""

    def reply_ok(self, status_code: int, body: str) -> bool:
        return status_code in self.success_statuses

    def hint(self, status_code: int, body: str) -> str:
        return ""

    async def _run(self, action: str, params: Any) -> ToolResult:
        if not isinstance(params, NotificationMessage):
            raise ToolContractError(
                f"notifier.{action} received unexpected params {type(params)}"
            )
        payload = self.render(params)
        try:
            response = await self._post(payload)
        except httpx.TimeoutException as exc:
            # Failing to connect in time means nothing was sent; a later timeout does not.
            raise TransientToolError(
                f"{self.provider} webhook timed out",
                outcome_unknown=not isinstance(exc, httpx.ConnectTimeout),
            ) from exc
        except httpx.HTTPError as exc:
            raise TransientToolError(
                self._redact(
                    f"{self.provider} webhook request failed ({type(exc).__name__}): {exc}"
                ),
                outcome_unknown=not isinstance(exc, httpx.ConnectError),
            ) from exc

        body = response.text.strip()
        if not self.reply_ok(response.status_code, body):
            self._raise_for_reply(response.status_code, body)
        return ToolResult(
            summary=f"Posted '{params.title}' to {self.provider}",
            output={
                "provider": self.provider,
                "status_code": response.status_code,
                "delivery_confirmed": self.delivery_confirmed,
            },
        )

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        if self._http is not None:
            return await self._http.post(self._url, json=payload)
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            return await client.post(self._url, json=payload)

    def _raise_for_reply(self, status_code: int, body: str) -> None:
        hint = self.hint(status_code, body)
        message = self._redact(
            f"{self.provider} webhook answered HTTP {status_code}: {body[:MAX_ERROR_CHARS]!r}"
            + (f" ({hint})" if hint else "")
        )
        # Rate limits and server errors are temporary; everything else will not fix itself.
        if status_code == 429:
            raise TransientToolError(
                message, outcome_unknown=False
            )  # "not now": it did not run
        if status_code >= 500:
            raise TransientToolError(message)
        raise FatalToolError(message)

    def _redact(self, text: str) -> str:
        # The webhook URL is a secret (anyone holding it can post), so it never appears in errors.
        return text.replace(self._url, "***")


def get_notifier(
    settings: Settings, http: httpx.AsyncClient | None = None
) -> WebhookNotifier:
    """Build the notifier for NOTIFIER_PROVIDER (providers are imported only when selected)."""
    url = settings.notifier_webhook_url
    if url is None:
        raise FatalToolError(
            f"No webhook URL configured for provider {settings.notifier_provider!r}"
        )
    secret = url.get_secret_value()
    if settings.notifier_provider == "slack":
        from app.tools.slack import SlackNotifier

        return SlackNotifier(secret, http)
    if settings.notifier_provider == "teams":
        from app.tools.teams import TeamsNotifier

        return TeamsNotifier(secret, http)
    # Unreachable while NotifierProvider is validated, but fails loudly if one is added unwired.
    raise ValueError(f"Unsupported notifier provider: {settings.notifier_provider!r}")
