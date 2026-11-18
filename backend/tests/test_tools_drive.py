from typing import Any

import pytest

from app.tools.base import FatalToolError, TransientToolError, UndoHandle
from app.tools.drive import MAX_CONTENT_CHARS, DriveAdapter, parse_listing

DEFAULT_FOLDER = "sandbox-folder-id"
FOLDER_ICON = "\N{FILE FOLDER}"
FILE_ICON = "\N{PAGE FACING UP}"


def created(name: str = "notes.md", file_id: str = "file-abc123") -> str:
    return f"Created file: {name}\nID: {file_id}"


def listing(*lines: str, more: bool = False) -> str:
    text = "Contents of folder:\n\n" + "\n".join(lines)
    return text + ("\n\nMore items available. Use pageToken: tok" if more else "")


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


def adapter(mcp: FakeMcp) -> DriveAdapter:
    return DriveAdapter(mcp, DEFAULT_FOLDER)


async def test_create_sends_the_expected_request_to_the_default_folder() -> None:
    mcp = FakeMcp(created())
    await adapter(mcp).execute(
        "create_text_file", {"name": "notes.md", "content": "# Notes"}
    )
    assert mcp.calls == [
        (
            "drive",
            "createTextFile",
            {
                "name": "notes.md",
                "content": "# Notes",
                "parentFolderId": DEFAULT_FOLDER,
            },
        )
    ]


async def test_create_returns_the_id_a_link_and_an_undo_handle() -> None:
    result = await adapter(FakeMcp(created(file_id="abc"))).execute(
        "create_text_file", {"name": "notes.md"}
    )
    assert result.output == {"file_id": "abc", "name": "notes.md"}
    assert result.url == "https://drive.google.com/file/d/abc/view"
    assert result.undo_handle == UndoHandle(
        tool="drive", action="create_text_file", data={"file_id": "abc"}
    )


async def test_folder_can_be_overridden_per_step() -> None:
    mcp = FakeMcp(created())
    await adapter(mcp).execute(
        "create_text_file", {"name": "notes.txt", "folder_id": "other-folder"}
    )
    assert mcp.calls[0][2]["parentFolderId"] == "other-folder"


@pytest.mark.parametrize(
    "params",
    [
        {"name": "notes"},  # no extension
        {"name": "report.docx"},  # the server accepts only .txt and .md
        {"name": "a\nb.md"},
        {"name": "dir/notes.md"},
        {"name": ".md" * 100 + "x.md"},  # too long
        {"name": ""},
        {"name": "ok.md", "content": "x" * (MAX_CONTENT_CHARS + 1)},
        {},
    ],
)
async def test_invalid_input_is_fatal_and_never_reaches_drive(params: dict) -> None:
    mcp = FakeMcp()
    with pytest.raises(FatalToolError, match="Invalid parameters"):
        await adapter(mcp).execute("create_text_file", params)
    assert mcp.calls == []


async def test_extension_check_is_case_insensitive() -> None:
    result = await adapter(FakeMcp(created("NOTES.MD"))).execute(
        "create_text_file", {"name": "NOTES.MD"}
    )
    assert result.undo_handle is not None


@pytest.mark.parametrize(
    "reply", ["Created file: notes.md\nID: unknown", "Created file: notes.md"]
)
async def test_missing_id_falls_back_to_finding_the_file_by_name(reply: str) -> None:
    mcp = FakeMcp(
        reply,
        listing(
            f"{FILE_ICON} other.md (ID: x1)", f"{FILE_ICON} notes.md (ID: found-id)"
        ),
    )
    result = await adapter(mcp).execute("create_text_file", {"name": "notes.md"})
    assert result.output["file_id"] == "found-id"
    assert mcp.calls[1][1] == "listFolder"


async def test_an_undeterminable_id_says_which_file_to_delete_by_hand() -> None:
    mcp = FakeMcp("Created file: notes.md\nID: unknown", listing())
    with pytest.raises(FatalToolError, match="delete it by hand"):
        await adapter(mcp).execute("create_text_file", {"name": "notes.md"})


async def test_the_servers_duplicate_name_refusal_passes_through_unchanged() -> None:
    refusal = FatalToolError('A file named "notes.md" already exists in this location.')
    with pytest.raises(FatalToolError, match="already exists"):
        await adapter(FakeMcp(refusal)).execute(
            "create_text_file", {"name": "notes.md"}
        )


async def test_transient_errors_from_the_session_pass_through_unchanged() -> None:
    with pytest.raises(TransientToolError, match="503"):
        await adapter(FakeMcp(TransientToolError("503 unavailable"))).execute(
            "create_text_file", {"name": "notes.md"}
        )


async def test_undo_moves_the_file_to_trash() -> None:
    mcp = FakeMcp("Successfully moved to trash: notes.md")
    handle = UndoHandle(
        tool="drive", action="create_text_file", data={"file_id": "abc"}
    )
    await adapter(mcp).undo(handle)
    assert mcp.calls == [("drive", "deleteItem", {"itemId": "abc"})]


async def test_undo_without_a_file_id_is_fatal() -> None:
    with pytest.raises(FatalToolError, match="no file_id"):
        await adapter(FakeMcp()).undo(
            UndoHandle(tool="drive", action="create_text_file")
        )


async def test_undo_rejects_the_read_only_action() -> None:
    with pytest.raises(FatalToolError, match="cannot be undone"):
        await adapter(FakeMcp()).undo(UndoHandle(tool="drive", action="list_folder"))


async def test_list_folder_returns_items_with_ids_and_folder_flags() -> None:
    mcp = FakeMcp(
        listing(
            f"{FOLDER_ICON} Archive (ID: folder-9)",
            f"{FILE_ICON} notes.md (ID: file-1)",
        )
    )
    result = await adapter(mcp).execute("list_folder", {})
    assert result.output["items"] == [
        {"id": "folder-9", "name": "Archive", "is_folder": True},
        {"id": "file-1", "name": "notes.md", "is_folder": False},
    ]
    assert result.output["more_available"] is False
    assert mcp.calls[0][2] == {"folderId": DEFAULT_FOLDER}


def test_an_empty_folder_parses_to_no_items() -> None:
    assert parse_listing("Contents of folder:\n\n") == ([], False)


def test_names_containing_parentheses_still_parse() -> None:
    items, _ = parse_listing(listing(f"{FILE_ICON} report (final) v2.md (ID: id-7)"))
    assert items == [{"id": "id-7", "name": "report (final) v2.md", "is_folder": False}]


def test_the_more_items_notice_is_reported() -> None:
    _, more = parse_listing(listing(f"{FILE_ICON} a.md (ID: 1)", more=True))
    assert more is True


def test_only_creating_a_file_is_reversible() -> None:
    reversible = {
        name for name, spec in DriveAdapter.actions.items() if spec.reversible
    }
    assert reversible == {"create_text_file"}
