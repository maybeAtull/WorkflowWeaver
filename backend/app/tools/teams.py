"""Microsoft Teams notifier: renders a NotificationMessage as an Adaptive Card for a Workflows webhook.

Format notes (from Microsoft's documentation and Q&A): the webhook expects a "message" envelope
whose `attachments` hold the card with contentType application/vnd.microsoft.card.adaptive and
`contentUrl` null. Action.Submit is not supported through webhooks, and a card height of
"automatic" was reported to cause HTTP 400, so the card uses only TextBlock and FactSet, with
links as Markdown text and no actions. The webhook can answer 202 without the message actually
appearing, so a success reply is reported as "accepted", not "delivered".
"""

from typing import Any, ClassVar

from app.tools.notifier import NotificationMessage, WebhookNotifier, clip

CARD_VERSION = "1.4"  # widely supported in Teams
TITLE_LIMIT = 300
TEXT_LIMIT = 3000

STATUS_ICON = {
    "success": "\N{WHITE HEAVY CHECK MARK}",
    "warning": "\N{WARNING SIGN}",
    "failure": "\N{CROSS MARK}",
}
STATUS_COLOR = {"success": "Good", "warning": "Warning", "failure": "Attention"}

ERROR_HINTS = {
    400: "the card was rejected; check Adaptive Card compatibility",
    401: "the webhook URL is not authorised; create a new one",
    403: "the webhook URL is not authorised; create a new one",
    404: "the webhook URL is invalid or the workflow was removed; create a new one",
}


def _markdown_link(label: str, url: str) -> str:
    # ']' would end the label and ')' would end the URL, so both are neutralised.
    return f"[{label.replace(']', ')')}]({url.replace(')', '%29')})"


class TeamsNotifier(WebhookNotifier):
    provider: ClassVar[str] = "teams"
    success_statuses: ClassVar[frozenset[int]] = frozenset({200, 202})
    delivery_confirmed: ClassVar[bool] = False

    def render(self, message: NotificationMessage) -> dict[str, Any]:
        body: list[dict[str, Any]] = [
            {
                "type": "TextBlock",
                "text": clip(
                    f"{STATUS_ICON[message.status]} {message.title}", TITLE_LIMIT
                ),
                "weight": "Bolder",
                "size": "Medium",
                "color": STATUS_COLOR[message.status],
                "wrap": True,
            }
        ]
        if message.summary:
            body.append(
                {
                    "type": "TextBlock",
                    "text": clip(message.summary, TEXT_LIMIT),
                    "wrap": True,
                }
            )
        if message.facts:
            body.append(
                {
                    "type": "FactSet",
                    "facts": [
                        {"title": f.name, "value": f.value} for f in message.facts
                    ],
                }
            )
        if message.links:
            links = "\n\n".join(
                _markdown_link(link.label, link.url) for link in message.links
            )
            body.append(
                {"type": "TextBlock", "text": clip(links, TEXT_LIMIT), "wrap": True}
            )

        card = {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": CARD_VERSION,
            "body": body,
        }
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": card,
                }
            ],
        }

    def hint(self, status_code: int, body: str) -> str:
        return ERROR_HINTS.get(status_code, "")
