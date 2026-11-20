from typing import Any

import pytest

from app.config import Settings
from app.tools.base import FatalToolError, UndoHandle
from app.tools.registry import ToolRegistry, build_registry
from app.tools.slack import SlackNotifier
from app.tools.teams import TeamsNotifier
from tests.test_config import FAKE_ENV

# The catalogue goes into every planner prompt; Groq's free tier allows only 8K tokens a minute.
MAX_CATALOGUE_CHARS = 2000


class FakeMcp:
    """Records tool calls and answers each with a canned reply."""

    def __init__(self, *replies: str) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._replies = list(replies)

    async def call_tool(
        self, server: str, tool: str, arguments: dict[str, Any] | None = None
    ) -> str:
        self.calls.append((server, tool, arguments or {}))
        return self._replies.pop(0)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def registry(settings: Settings) -> ToolRegistry:
    return build_registry(settings, FakeMcp())


# ------------------------------------------------------------------ building


def test_the_registry_holds_the_three_tools(registry: ToolRegistry) -> None:
    for tool in ("notion", "drive", "notifier"):
        assert registry.adapter(tool).name == tool


def test_the_notifier_follows_the_configured_provider(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOTIFIER_PROVIDER", "slack")
    slack = build_registry(Settings(_env_file=None), FakeMcp())  # type: ignore[call-arg]
    assert isinstance(slack.adapter("notifier"), SlackNotifier)
    monkeypatch.setenv("NOTIFIER_PROVIDER", "teams")
    teams = build_registry(Settings(_env_file=None), FakeMcp())  # type: ignore[call-arg]
    assert isinstance(teams.adapter("notifier"), TeamsNotifier)


def test_duplicate_tool_names_are_rejected(registry: ToolRegistry) -> None:
    notion = registry.adapter("notion")
    with pytest.raises(ValueError, match="Duplicate tool name"):
        ToolRegistry([notion, notion])


def test_building_the_registry_calls_no_tool(settings: Settings) -> None:
    mcp = FakeMcp()
    build_registry(settings, mcp)
    assert mcp.calls == []  # servers start lazily, on a tool's first call


# ------------------------------------------------------------------ catalogue


def test_the_catalogue_lists_the_actions_the_planner_may_use(
    registry: ToolRegistry,
) -> None:
    catalogue = registry.render_catalogue()
    for action in ("notion.create_page(", "drive.create_text_file(", "notifier.send("):
        assert action in catalogue


def test_verification_only_actions_are_hidden_from_the_planner(
    registry: ToolRegistry,
) -> None:
    catalogue = registry.render_catalogue()
    assert "get_page" not in catalogue
    assert "list_folder" not in catalogue


def test_settings_supplied_ids_are_hidden_so_the_llm_cannot_invent_them(
    registry: ToolRegistry,
) -> None:
    catalogue = registry.render_catalogue()
    assert "parent_page_id" not in catalogue
    assert "folder_id" not in catalogue


def test_required_parameters_are_starred_and_nested_shapes_are_compact(
    registry: ToolRegistry,
) -> None:
    lines = {
        line.split("(")[0]: line for line in registry.render_catalogue().splitlines()
    }
    assert "title*: text" in lines["notion.create_page"]
    assert "name*: text" in lines["drive.create_text_file"]
    assert "title*: text" in lines["notifier.send"]
    # A nested shape is rendered compactly (this one is a Notion list parameter).
    assert "action_items: list of text" in lines["notion.create_page"]


def test_the_planner_cannot_see_what_the_executor_fills_in_for_the_notification(
    registry: ToolRegistry,
) -> None:
    send = next(
        line
        for line in registry.render_catalogue().splitlines()
        if line.startswith("notifier.send")
    )
    for hidden in ("status", "facts", "links"):
        assert hidden not in send.split(" - ")[0]
    problems = registry.validate_step(
        "notifier",
        "send",
        {"title": "ok", "links": [{"label": "a", "url": "https://x"}]},
    )
    assert any("unknown parameter(s) ['links']" in p for p in problems)


def test_the_catalogue_stays_small_enough_for_a_free_tier_prompt(
    registry: ToolRegistry,
) -> None:
    assert len(registry.render_catalogue()) <= MAX_CATALOGUE_CHARS


# ------------------------------------------------------------------ validation


def test_a_valid_step_has_no_problems(registry: ToolRegistry) -> None:
    assert registry.validate_step("notion", "create_page", {"title": "Plan"}) == []
    assert registry.validate_step("drive", "create_text_file", {"name": "n.md"}) == []
    assert (
        registry.validate_step(
            "notifier", "send", {"title": "Done", "summary": "All finished."}
        )
        == []
    )


def test_an_unknown_tool_is_reported_with_the_available_ones(
    registry: ToolRegistry,
) -> None:
    [problem] = registry.validate_step("slack", "send", {})
    assert "unknown tool 'slack'" in problem
    assert "notion" in problem


@pytest.mark.parametrize(
    ("tool", "action"),
    [("notion", "explode"), ("notion", "get_page"), ("drive", "list_folder")],
)
def test_unknown_and_hidden_actions_are_reported_as_unknown(
    registry: ToolRegistry, tool: str, action: str
) -> None:
    [problem] = registry.validate_step(tool, action, {})
    assert f"unknown action '{tool}.{action}'" in problem
    suggestions = problem.split("available:")[1]
    assert "get_page" not in suggestions  # hidden actions are never suggested
    assert "list_folder" not in suggestions


def test_an_invented_parameter_is_rejected_not_silently_dropped(
    registry: ToolRegistry,
) -> None:
    problems = registry.validate_step(
        "notion", "create_page", {"title": "Plan", "colour": "red"}
    )
    assert any("unknown parameter(s) ['colour']" in p for p in problems)


@pytest.mark.parametrize(
    ("tool", "action", "hidden"),
    [
        ("notion", "create_page", "parent_page_id"),
        ("drive", "create_text_file", "folder_id"),
    ],
)
def test_the_planner_may_not_set_a_settings_supplied_id(
    registry: ToolRegistry, tool: str, action: str, hidden: str
) -> None:
    title_key = "title" if tool == "notion" else "name"
    value = "Plan" if tool == "notion" else "n.md"
    problems = registry.validate_step(
        tool, action, {title_key: value, hidden: "made-up-id"}
    )
    assert any(f"['{hidden}']" in p for p in problems)


@pytest.mark.parametrize(
    ("tool", "action", "params", "expected"),
    [
        ("notion", "create_page", {}, "title"),
        ("drive", "create_text_file", {"name": "report.docx"}, "name"),
        ("notifier", "send", {"title": "ok", "status": "great"}, "status"),
        (
            "notifier",
            "send",
            {"title": "ok", "links": [{"label": "a", "url": "ftp://x"}]},
            "links",
        ),
    ],
)
def test_bad_values_are_reported_by_field(
    registry: ToolRegistry, tool: str, action: str, params: dict, expected: str
) -> None:
    problems = registry.validate_step(tool, action, params)
    assert problems
    assert any(f"{tool}.{action}.{expected}" in p for p in problems)


def test_problem_messages_never_echo_the_offending_value(
    registry: ToolRegistry,
) -> None:
    problems = registry.validate_step(
        "notion", "create_page", {"title": ["SECRET-USER-TEXT"]}
    )
    assert problems
    assert "SECRET-USER-TEXT" not in " ".join(problems)


# ------------------------------------------------------------------ execution and undo


async def test_execute_routes_to_the_right_adapter(settings: Settings) -> None:
    mcp = FakeMcp('{"id": "page-1", "url": "https://notion.so/page-1"}')
    result = await build_registry(settings, mcp).execute(
        "notion", "create_page", {"title": "Plan"}
    )
    assert result.output["page_id"] == "page-1"
    assert mcp.calls[0][:2] == ("notion", "API-post-page")


async def test_execute_can_run_verification_actions_the_planner_never_sees(
    settings: Settings,
) -> None:
    mcp = FakeMcp('{"id": "page-1", "in_trash": false}')
    result = await build_registry(settings, mcp).execute(
        "notion", "get_page", {"page_id": "page-1"}
    )
    assert result.output["page_id"] == "page-1"


@pytest.mark.parametrize(("tool", "action"), [("slack", "send"), ("notion", "explode")])
async def test_execute_reports_an_unknown_tool_or_action(
    registry: ToolRegistry, tool: str, action: str
) -> None:
    with pytest.raises(FatalToolError):
        await registry.execute(tool, action, {})


async def test_undo_routes_by_the_tool_named_in_the_handle(settings: Settings) -> None:
    mcp = FakeMcp("trashed")
    handle = UndoHandle(tool="drive", action="create_text_file", data={"file_id": "f1"})
    await build_registry(settings, mcp).undo(handle)
    assert mcp.calls == [("drive", "deleteItem", {"itemId": "f1"})]


async def test_undo_for_an_unknown_tool_is_fatal(registry: ToolRegistry) -> None:
    with pytest.raises(FatalToolError, match="Unknown tool"):
        await registry.undo(UndoHandle(tool="ghost", action="x"))


# ------------------------------------------------------------------ ordering


def test_only_the_notification_must_end_the_plan(registry: ToolRegistry) -> None:
    assert registry.must_be_last("notifier", "send") is True
    assert registry.must_be_last("notion", "create_page") is False
    assert registry.must_be_last("drive", "create_text_file") is False
