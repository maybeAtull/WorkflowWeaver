"""Smoke test for the Slack incoming webhook.

Run from the backend/ folder:  python -m scripts.smoke_slack

Posts ONE synthetic test message to the channel behind SLACK_WEBHOOK_URL. A webhook post
cannot be undone through the webhook (delete it by hand in Slack). Never prints the URL.
Exits non-zero on failure.
"""

import sys

import httpx
from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import ENV_FILE

REQUEST_TIMEOUT_SECONDS = 15
SLACK_WEBHOOK_PREFIX = "https://hooks.slack.com/"
MESSAGE = "WorkflowWeaver smoke test: if you can read this, the Slack webhook works."

# Slack's documented plain-text error bodies for incoming webhooks.
SLACK_ERROR_HINTS = {
    "invalid_payload": "Slack rejected the message body",
    "no_text": "message had no text",
    "invalid_token": "webhook URL is invalid or was revoked; create a new one",
    "no_service": "webhook is disabled or was removed; create a new one",
    "channel_not_found": "the channel the webhook posts to no longer exists",
    "channel_is_archived": "the channel the webhook posts to is archived",
    "action_prohibited": "workspace settings block this webhook",
}


class SlackSettings(BaseSettings):
    """Only what this script needs, so a Slack test doesn't demand every other credential."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )
    slack_webhook_url: SecretStr


def report(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    return ok


def post_test_message(url: str) -> bool:
    try:
        response = httpx.post(
            url, json={"text": MESSAGE}, timeout=REQUEST_TIMEOUT_SECONDS
        )
    except httpx.HTTPError as exc:
        # httpx messages can include the request URL, which is a secret here.
        message = str(exc).replace(url, "***")
        return report("post message", False, f"{type(exc).__name__}: {message[:300]}")

    body = response.text.strip()
    if response.status_code == 200 and body == "ok":
        return report("post message", True, "Slack answered 200 ok; check the channel")
    hint = SLACK_ERROR_HINTS.get(body, "unexpected response")
    return report(
        "post message", False, f"HTTP {response.status_code} {body!r} ({hint})"
    )


def main() -> int:
    try:
        settings = SlackSettings()
    except ValidationError as exc:
        # Field names and messages only; never the offending input values.
        problems = [
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        ]
        report("config loads", False, "; ".join(problems))
        return 1

    url = settings.slack_webhook_url.get_secret_value().strip()
    if not url.startswith(SLACK_WEBHOOK_PREFIX):
        # Catches the template placeholder before we send anything anywhere.
        report(
            "config loads",
            False,
            f"SLACK_WEBHOOK_URL does not start with {SLACK_WEBHOOK_PREFIX} "
            "(is it still the placeholder?)",
        )
        return 1
    report("config loads", True, "SLACK_WEBHOOK_URL looks like a Slack webhook")
    return 0 if post_test_message(url) else 1


if __name__ == "__main__":
    sys.exit(main())
