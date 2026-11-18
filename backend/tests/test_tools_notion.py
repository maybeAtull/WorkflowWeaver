import json
from typing import Any

import pytest

from app.tools.base import FatalToolError, TransientToolError, UndoHandle
from app.tools.notion import MAX_BLOCK_TEXT, MAX_BLOCKS, NotionAdapter

DEFAULT_PARENT = "sandbox-page-id"


def page_json(
    page_id: str = "page-123", *, title: str = "My title", in_trash: bool = False
) -> str:
    return json.dumps(
        {
            "object": "page",
            "id": page_id,
            "url": f"https://www.notion.so/{page_id}",
            "in_trash": in_trash,
            "properties": {
                "title": {"type": "title", "title": [{"plain_text": title}]}
            },
        }
    )


class FakeMcp:
    """Records tool calls and replays canned replies (a string, or an exception to raise)."""

    def __init__(self, *replies: str | Exception) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._replies = list(replies)

    async def call_tool(
        self, server: str, tool: str, arguments: dict[str, Any] | None = None
    ) -> str:
        self.calls.append((server, tool, arguments or {}))
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def adapter(mcp: FakeMcp) -> NotionAdapter:
    return NotionAdapter(mcp, DEFAULT_PARENT)


async def test_create_page_sends_the_expected_request() -> None:
    mcp = FakeMcp(page_json())
    await adapter(mcp).execute(
        "create_page",
        {
            "title": "Sprint plan",
            "content": "First paragraph.\n\nSecond paragraph.",
            "action_items": ["Write FAQ", "Book room"],
        },
    )
    server, tool, arguments = mcp.calls[0]
    assert (server, tool) == ("notion", "API-post-page")
    assert arguments["parent"] == {"page_id": DEFAULT_PARENT}
    assert arguments["properties"] == {
        "title": [{"type": "text", "text": {"content": "Sprint plan"}}]
    }
    kinds = [block["type"] for block in arguments["children"]]
    assert kinds == ["paragraph", "paragraph", "to_do", "to_do"]
    assert arguments["children"][2]["to_do"]["checked"] is False
    assert (
        arguments["children"][3]["to_do"]["rich_text"][0]["text"]["content"]
        == "Book room"
    )


async def test_create_page_returns_the_link_and_an_undo_handle() -> None:
    result = await adapter(FakeMcp(page_json("abc"))).execute(
        "create_page", {"title": "Plan"}
    )
    assert result.url == "https://www.notion.so/abc"
    assert result.undo_handle == UndoHandle(
        tool="notion", action="create_page", data={"page_id": "abc"}
    )
    assert result.output["page_id"] == "abc"


async def test_parent_page_can_be_overridden_per_step() -> None:
    mcp = FakeMcp(page_json())
    await adapter(mcp).execute(
        "create_page", {"title": "Plan", "parent_page_id": "other-parent"}
    )
    assert mcp.calls[0][2]["parent"] == {"page_id": "other-parent"}


async def test_long_paragraphs_are_split_to_notions_block_limit() -> None:
    mcp = FakeMcp(page_json())
    await adapter(mcp).execute(
        "create_page", {"title": "Plan", "content": "x" * (MAX_BLOCK_TEXT + 10)}
    )
    sizes = [
        len(block["paragraph"]["rich_text"][0]["text"]["content"])
        for block in mcp.calls[0][2]["children"]
    ]
    assert sizes == [MAX_BLOCK_TEXT, 10]


async def test_empty_body_sends_no_paragraph_blocks() -> None:
    mcp = FakeMcp(page_json())
    await adapter(mcp).execute("create_page", {"title": "Plan"})
    assert mcp.calls[0][2]["children"] == []


async def test_too_many_blocks_fails_before_calling_notion() -> None:
    mcp = FakeMcp()
    paragraphs = "\n\n".join(f"para {i}" for i in range(MAX_BLOCKS + 1))
    with pytest.raises(FatalToolError, match="blocks"):
        await adapter(mcp).execute(
            "create_page", {"title": "Plan", "content": paragraphs}
        )
    assert mcp.calls == []


@pytest.mark.parametrize(
    "params",
    [
        {"title": ""},
        {"title": "x" * 201},
        {"title": "ok", "action_items": [""]},
        {"title": "ok", "action_items": ["a"] * 51},
        {},
    ],
)
async def test_invalid_input_is_fatal_and_never_reaches_notion(params: dict) -> None:
    mcp = FakeMcp()
    with pytest.raises(FatalToolError, match="Invalid parameters"):
        await adapter(mcp).execute("create_page", params)
    assert mcp.calls == []


@pytest.mark.parametrize("reply", ["not json at all", json.dumps({"object": "page"})])
async def test_an_unreadable_create_reply_says_the_page_may_exist(reply: str) -> None:
    with pytest.raises(FatalToolError, match="may exist; check Notion by hand"):
        await adapter(FakeMcp(reply)).execute("create_page", {"title": "Plan"})


async def test_transient_errors_from_the_session_pass_through_unchanged() -> None:
    with pytest.raises(TransientToolError, match="429"):
        await adapter(FakeMcp(TransientToolError("429 rate limited"))).execute(
            "create_page", {"title": "Plan"}
        )


async def test_undo_moves_the_page_to_trash() -> None:
    mcp = FakeMcp(page_json())
    notion = adapter(mcp)
    await notion.undo(
        UndoHandle(tool="notion", action="create_page", data={"page_id": "abc"})
    )
    assert mcp.calls == [
        ("notion", "API-patch-page", {"page_id": "abc", "in_trash": True})
    ]


async def test_undo_without_a_page_id_is_fatal() -> None:
    with pytest.raises(FatalToolError, match="no page_id"):
        await adapter(FakeMcp()).undo(UndoHandle(tool="notion", action="create_page"))


async def test_undo_rejects_the_read_only_action() -> None:
    with pytest.raises(FatalToolError, match="cannot be undone"):
        await adapter(FakeMcp()).undo(UndoHandle(tool="notion", action="get_page"))


async def test_get_page_reports_title_link_and_trash_state() -> None:
    mcp = FakeMcp(page_json("abc", title="Sprint plan", in_trash=True))
    result = await adapter(mcp).execute("get_page", {"page_id": "abc"})
    assert result.output == {"page_id": "abc", "title": "Sprint plan", "in_trash": True}
    assert result.url == "https://www.notion.so/abc"
    assert "in trash" in result.summary
    assert result.undo_handle is None
    assert mcp.calls[0][1] == "API-retrieve-a-page"


async def test_get_page_with_an_unreadable_reply_is_fatal() -> None:
    with pytest.raises(FatalToolError, match="unexpected response"):
        await adapter(FakeMcp("garbage")).execute("get_page", {"page_id": "abc"})


def test_the_planner_sees_the_create_but_not_the_read_or_the_parent_id() -> None:
    create, read = (
        NotionAdapter.actions["create_page"],
        NotionAdapter.actions["get_page"],
    )
    assert create.planner_visible is True
    assert create.planner_hidden_params == {"parent_page_id"}  # comes from settings
    assert read.planner_visible is False  # used by the verify step only


def test_only_the_read_is_safe_to_repeat() -> None:
    """A repeated page create could make a second page, so it must never be retried blindly."""
    flags = {name: spec.repeat_safe for name, spec in NotionAdapter.actions.items()}
    assert flags == {"create_page": False, "get_page": True}


def test_only_creating_a_page_is_reversible() -> None:
    reversible = {
        name for name, spec in NotionAdapter.actions.items() if spec.reversible
    }
    assert reversible == {"create_page"}
