"""Slack notifier: renders a NotificationMessage as an Incoming Webhook payload with blocks."""

from typing import Any, ClassVar

from app.tools.notifier import NotificationMessage, WebhookNotifier, clip

SECTION_TEXT_LIMIT = 3000  # Slack's cap for a section block's text
FIELD_TEXT_LIMIT = 2000  # Slack's cap for each section field
FIELDS_PER_SECTION = 10  # Slack allows at most 10 fields in one section
FALLBACK_TEXT_LIMIT = 500

STATUS_ICON = {
    "success": ":white_check_mark:",
    "warning": ":warning:",
    "failure": ":x:",
}

# Slack's documented plain-text error bodies for incoming webhooks.
ERROR_HINTS = {
    "invalid_payload": "Slack rejected the message body",
    "no_text": "the message had no text",
    "invalid_token": "the webhook URL is invalid or was revoked; create a new one",
    "no_service": "the webhook is disabled or was removed; create a new one",
    "channel_not_found": "the channel the webhook posts to no longer exists",
    "channel_is_archived": "the channel the webhook posts to is archived",
    "action_prohibited": "workspace settings block this webhook",
}


def escape(text: str) -> str:
    """Escape the three characters Slack treats as control codes.

    Workflow text comes from users and the LLM. Unescaped, "<!channel>" would notify everyone
    in the channel, so every piece of text we send is escaped.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _link(label: str, url: str) -> str:
    # A "|" would end the URL part of <url|label>, so it is percent-encoded.
    return f"\N{BULLET} <{escape(url.replace('|', '%7C'))}|{escape(label)}>"


class SlackNotifier(WebhookNotifier):
    provider: ClassVar[str] = "slack"

    def render(self, message: NotificationMessage) -> dict[str, Any]:
        icon = STATUS_ICON[message.status]
        # The top-level text is parsed by Slack as well (it is the notification preview), so it
        # must be escaped too, or a "<!channel>" in it would still notify everyone.
        fallback = f"{icon} {escape(message.title)}" + (
            f": {escape(message.summary)}" if message.summary else ""
        )

        heading = f"{icon} *{escape(message.title)}*"
        if message.summary:
            heading += f"\n{escape(message.summary)}"
        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": clip(heading, SECTION_TEXT_LIMIT)},
            }
        ]

        fields = [
            {
                "type": "mrkdwn",
                "text": clip(
                    f"*{escape(f.name)}*\n{escape(f.value)}", FIELD_TEXT_LIMIT
                ),
            }
            for f in message.facts
        ]
        for start in range(0, len(fields), FIELDS_PER_SECTION):
            blocks.append(
                {
                    "type": "section",
                    "fields": fields[start : start + FIELDS_PER_SECTION],
                }
            )

        if message.links:
            links = "\n".join(_link(link.label, link.url) for link in message.links)
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": clip(links, SECTION_TEXT_LIMIT)},
                }
            )

        return {"text": clip(fallback, FALLBACK_TEXT_LIMIT), "blocks": blocks}

    def reply_ok(self, status_code: int, body: str) -> bool:
        return status_code == 200 and body == "ok"

    def hint(self, status_code: int, body: str) -> str:
        return ERROR_HINTS.get(body, "")
