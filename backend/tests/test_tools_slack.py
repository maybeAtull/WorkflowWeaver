import json
from collections.abc import Callable

import httpx
import pytest

from app.tools.base import FatalToolError, TransientToolError
from app.tools.notifier import NotificationMessage
from app.tools.slack import SECTION_TEXT_LIMIT, SlackNotifier, escape

URL = "https://hooks.example.invalid/services/SECRET-TOKEN"


def notifier(
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
) -> SlackNotifier:
    handler = handler or (lambda request: httpx.Response(200, text="ok"))
    return SlackNotifier(URL, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def render(**fields: object) -> dict:
    return SlackNotifier(URL).render(NotificationMessage(title="Done", **fields))


def test_basic_message_has_a_text_fallback_and_a_bold_title_section() -> None:
    payload = render(summary="All good")
    assert payload["text"] == ":white_check_mark: Done: All good"
    section = payload["blocks"][0]
    assert section["type"] == "section"
    assert section["text"] == {
        "type": "mrkdwn",
        "text": ":white_check_mark: *Done*\nAll good",
    }


@pytest.mark.parametrize(
    ("status", "icon"),
    [("success", ":white_check_mark:"), ("warning", ":warning:"), ("failure", ":x:")],
)
def test_status_maps_to_an_icon(status: str, icon: str) -> None:
    assert render(status=status)["text"].startswith(icon)


def test_user_text_cannot_trigger_a_channel_mention() -> None:
    payload = render(
        summary="<!channel> & <!here> <@U123>",
        facts=[{"name": "<!everyone>", "value": "<!channel>"}],
        links=[{"label": "<!channel>", "url": "https://example.com/?a=1&b=2"}],
    )
    dumped = json.dumps(payload)
    assert "<!channel>" not in dumped
    assert "<!here>" not in dumped
    assert "<!everyone>" not in dumped
    assert "&lt;!channel&gt;" in dumped


def test_escape_handles_all_three_control_characters() -> None:
    assert escape("a & b < c > d") == "a &amp; b &lt; c &gt; d"


def test_facts_become_fields_and_more_than_ten_split_into_extra_sections() -> None:
    facts = [{"name": f"n{i}", "value": f"v{i}"} for i in range(12)]
    blocks = render(facts=facts)["blocks"]
    field_sections = [block for block in blocks if "fields" in block]
    assert [len(block["fields"]) for block in field_sections] == [10, 2]
    assert field_sections[0]["fields"][0] == {"type": "mrkdwn", "text": "*n0*\nv0"}


def test_links_use_slack_syntax_with_escaped_urls_and_encoded_pipes() -> None:
    payload = render(
        links=[
            {"label": "Open", "url": "https://example.com/?a=1&b=2"},
            {"label": "Pipe", "url": "https://example.com/a|b"},
        ]
    )
    text = payload["blocks"][-1]["text"]["text"]
    assert "<https://example.com/?a=1&amp;b=2|Open>" in text
    assert "<https://example.com/a%7Cb|Pipe>" in text


def test_long_text_is_clipped_not_rejected() -> None:
    payload = render(summary="x" * 4000)
    text = payload["blocks"][0]["text"]["text"]
    assert len(text) == SECTION_TEXT_LIMIT
    assert text.endswith("\N{HORIZONTAL ELLIPSIS}")


def test_a_message_without_facts_or_links_has_a_single_block() -> None:
    assert len(render()["blocks"]) == 1


async def test_a_successful_send_posts_the_rendered_payload() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, text="ok")

    result = await notifier(handler).execute(
        "send", {"title": "Done", "summary": "Fine"}
    )
    assert seen == [render(summary="Fine")]
    assert result.output == {
        "provider": "slack",
        "status_code": 200,
        "delivery_confirmed": True,
    }
    assert result.undo_handle is None


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (400, "invalid_payload", "Slack rejected the message body"),
        (403, "invalid_token", "invalid or was revoked"),
        (404, "no_service", "disabled or was removed"),
        (404, "channel_not_found", "no longer exists"),
        (200, "something else", "answered HTTP 200"),  # 200 without "ok" is not success
    ],
)
async def test_slack_errors_are_fatal_with_plain_hints(
    status: int, body: str, expected: str
) -> None:
    handler = lambda request: httpx.Response(status, text=body)
    with pytest.raises(FatalToolError, match=expected):
        await notifier(handler).execute("send", {"title": "Done"})


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_rate_limits_and_server_errors_are_transient(status: int) -> None:
    handler = lambda request: httpx.Response(status, text="try later")
    with pytest.raises(TransientToolError):
        await notifier(handler).execute("send", {"title": "Done"})
