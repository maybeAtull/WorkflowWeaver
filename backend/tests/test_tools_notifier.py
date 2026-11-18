from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.tools.base import FatalToolError, TransientToolError, UndoHandle
from app.tools.notifier import NotificationMessage, clip, get_notifier
from app.tools.slack import SlackNotifier
from app.tools.teams import TeamsNotifier
from tests.test_config import FAKE_ENV

SLACK_URL = "https://hooks.example.invalid/services/SECRET-TOKEN"


def mock_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_clip_leaves_short_text_and_marks_long_text_with_an_ellipsis() -> None:
    assert clip("short", 10) == "short"
    clipped = clip("x" * 50, 10)
    assert len(clipped) == 10
    assert clipped.endswith("\N{HORIZONTAL ELLIPSIS}")


def test_the_tool_is_called_notifier_with_one_irreversible_send_action() -> None:
    for cls in (SlackNotifier, TeamsNotifier):
        assert cls.name == "notifier"
        assert set(cls.actions) == {"send"}
        assert cls.actions["send"].reversible is False


async def test_a_sent_message_cannot_be_undone() -> None:
    notifier = SlackNotifier(SLACK_URL)
    with pytest.raises(FatalToolError, match="cannot be undone"):
        await notifier.undo(UndoHandle(tool="notifier", action="send"))


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"title": ""},
        {"title": "ok", "links": [{"label": "x", "url": "javascript:alert(1)"}]},
        {"title": "ok", "facts": [{"name": "n", "value": "v"}] * 21},
        {"title": "ok", "status": "unknown"},
    ],
)
async def test_invalid_messages_are_fatal_and_never_sent(params: dict) -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, text="ok")

    notifier = SlackNotifier(SLACK_URL, mock_client(handler))
    with pytest.raises(FatalToolError, match="Invalid parameters"):
        await notifier.execute("send", params)
    assert sent == []


async def test_a_network_error_is_transient_and_never_leaks_the_webhook_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {SLACK_URL}")

    notifier = SlackNotifier(SLACK_URL, mock_client(handler))
    with pytest.raises(TransientToolError) as exc_info:
        await notifier.execute("send", {"title": "Done"})
    assert SLACK_URL not in str(exc_info.value)
    assert "***" in str(exc_info.value)


async def test_a_timeout_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    notifier = TeamsNotifier(SLACK_URL, mock_client(handler))
    with pytest.raises(TransientToolError, match="timed out"):
        await notifier.execute("send", {"title": "Done"})


async def test_an_error_body_that_echoes_the_url_is_scrubbed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text=f"bad request to {SLACK_URL}")

    notifier = TeamsNotifier(SLACK_URL, mock_client(handler))
    with pytest.raises(FatalToolError) as exc_info:
        await notifier.execute("send", {"title": "Done"})
    assert SLACK_URL not in str(exc_info.value)


def test_get_notifier_follows_the_configured_provider(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOTIFIER_PROVIDER", "slack")
    assert isinstance(get_notifier(Settings(_env_file=None)), SlackNotifier)  # type: ignore[call-arg]
    monkeypatch.setenv("NOTIFIER_PROVIDER", "teams")
    assert isinstance(get_notifier(Settings(_env_file=None)), TeamsNotifier)  # type: ignore[call-arg]


def test_get_notifier_uses_the_selected_providers_webhook_url(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOTIFIER_PROVIDER", "slack")
    notifier = get_notifier(Settings(_env_file=None))  # type: ignore[call-arg]
    assert notifier._url == FAKE_ENV["SLACK_WEBHOOK_URL"]


def test_the_message_model_accepts_a_full_message() -> None:
    message = NotificationMessage(
        title="Done",
        summary="All steps finished",
        status="warning",
        facts=[{"name": "Notion", "value": "page"}],
        links=[{"label": "Open", "url": "https://example.com"}],
    )
    assert message.status == "warning"
