import json
from collections.abc import Callable

import httpx
import pytest

from app.tools.base import FatalToolError, TransientToolError
from app.tools.notifier import NotificationMessage
from app.tools.teams import TEXT_LIMIT, TeamsNotifier

URL = "https://example.invalid/workflows/SECRET-TOKEN"


def notifier(
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
) -> TeamsNotifier:
    handler = handler or (lambda request: httpx.Response(202))
    return TeamsNotifier(URL, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def render(**fields: object) -> dict:
    return TeamsNotifier(URL).render(NotificationMessage(title="Done", **fields))


def card(payload: dict) -> dict:
    return payload["attachments"][0]["content"]


def test_the_envelope_matches_the_documented_workflows_webhook_format() -> None:
    payload = render(summary="Fine")
    assert set(payload) == {"type", "attachments"}
    assert payload["type"] == "message"
    assert len(payload["attachments"]) == 1
    attachment = payload["attachments"][0]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert attachment["contentUrl"] is None
    content = attachment["content"]
    assert content["type"] == "AdaptiveCard"
    assert content["version"] == "1.4"
    assert content["$schema"] == "http://adaptivecards.io/schemas/adaptive-card.json"


def test_the_card_uses_only_elements_known_to_work_through_webhooks() -> None:
    payload = render(
        summary="Fine",
        facts=[{"name": "Notion", "value": "page"}],
        links=[{"label": "Open", "url": "https://example.com"}],
    )
    assert {element["type"] for element in card(payload)["body"]} <= {
        "TextBlock",
        "FactSet",
    }
    dumped = json.dumps(payload)
    assert "Action." not in dumped  # Action.Submit is unsupported through webhooks
    assert (
        "automatic" not in dumped
    )  # an "automatic" height was reported to cause HTTP 400
    assert "actions" not in card(payload)


@pytest.mark.parametrize(
    ("status", "color"),
    [("success", "Good"), ("warning", "Warning"), ("failure", "Attention")],
)
def test_status_colours_the_title(status: str, color: str) -> None:
    title = card(render(status=status))["body"][0]
    assert title["color"] == color
    assert title["weight"] == "Bolder"
    assert title["text"].endswith(" Done")


def test_facts_become_a_fact_set() -> None:
    body = card(render(facts=[{"name": "Notion", "value": "page"}]))["body"]
    assert body[-1] == {
        "type": "FactSet",
        "facts": [{"title": "Notion", "value": "page"}],
    }


def test_links_become_markdown_and_awkward_characters_are_neutralised() -> None:
    body = card(
        render(
            links=[
                {"label": "Open", "url": "https://example.com/x"},
                {"label": "Odd]label", "url": "https://example.com/a(b)"},
            ]
        )
    )["body"]
    text = body[-1]["text"]
    assert "[Open](https://example.com/x)" in text
    assert r"[Odd\]label](https://example.com/a(b%29)" in text


def test_long_text_is_clipped_not_rejected() -> None:
    summary = card(render(summary="x" * 4500))["body"][1]["text"]
    assert len(summary) == TEXT_LIMIT
    assert summary.endswith("\N{HORIZONTAL ELLIPSIS}")


@pytest.mark.parametrize("status", [200, 202])
async def test_success_is_reported_as_accepted_not_delivered(status: int) -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(status)

    result = await notifier(handler).execute(
        "send", {"title": "Done", "summary": "Fine"}
    )
    assert seen == [render(summary="Fine")]
    assert result.output == {
        "provider": "teams",
        "status_code": status,
        "delivery_confirmed": False,
    }


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, "Adaptive Card compatibility"),
        (401, "not authorised"),
        (403, "not authorised"),
        (404, "invalid or the workflow was removed"),
    ],
)
async def test_client_errors_are_fatal_with_plain_hints(
    status: int, expected: str
) -> None:
    handler = lambda request: httpx.Response(status, text="no")
    with pytest.raises(FatalToolError, match=expected):
        await notifier(handler).execute("send", {"title": "Done"})


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_rate_limits_and_server_errors_are_transient(status: int) -> None:
    handler = lambda request: httpx.Response(status, text="try later")
    with pytest.raises(TransientToolError):
        await notifier(handler).execute("send", {"title": "Done"})
