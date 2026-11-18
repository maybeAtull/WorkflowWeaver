"""Google Drive adapter: create a text file (reversible) and list a folder.

Talks to Drive only through the MCP session manager, using three whitelisted tools. The reply
formats below were read from the pinned server's source (google-drive-mcp 2.13.0), not guessed:
  createTextFile -> "Created file: <name>\\nID: <id>"
  listFolder     -> "Contents of folder:\\n\\n<icon> <name> (ID: <id>)" per item
Undo of a created file is "move to trash" (restorable). The server also refuses to create a
file whose name already exists in the folder, so a retried create cannot make a duplicate.
"""

import re
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel, Field, field_validator

from app.tools.base import (
    ActionSpec,
    FatalToolError,
    ToolAdapter,
    ToolContractError,
    ToolResult,
    UndoHandle,
)
from app.tools.mcp_servers import DRIVE_SERVER

CREATE_TEXT_FILE_TOOL = "createTextFile"
LIST_FOLDER_TOOL = "listFolder"
DELETE_ITEM_TOOL = "deleteItem"  # moves to trash (restorable): our undo

ALLOWED_EXTENSIONS = (".txt", ".md")  # the server rejects any other text-file name
MAX_CONTENT_CHARS = 50_000

_CREATED_ID = re.compile(r"^ID:\s*(\S+)\s*$", re.MULTILINE)
# "<icon> <name> (ID: <id>)": the icon is a folder or document emoji, so match it loosely.
_LISTED_ITEM = re.compile(r"^(\S+)\s+(.+?)\s+\(ID:\s*([^)\s]+)\)\s*$", re.MULTILINE)
_FOLDER_ICON = "\N{FILE FOLDER}"


class McpCaller(Protocol):
    """What the adapter needs from the session manager (lets tests pass a fake)."""

    async def call_tool(
        self, server: str, tool: str, arguments: dict[str, Any] | None = None
    ) -> str: ...


class CreateTextFileParams(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    content: str = Field(default="", max_length=MAX_CONTENT_CHARS)
    folder_id: str | None = None  # defaults to the sandbox folder from settings

    @field_validator("name")
    @classmethod
    def _valid_file_name(cls, name: str) -> str:
        if any(ch in name for ch in "\r\n/\\"):
            raise ValueError("file name must not contain line breaks or slashes")
        if not name.lower().endswith(ALLOWED_EXTENSIONS):
            raise ValueError(f"file name must end with one of {ALLOWED_EXTENSIONS}")
        return name


class ListFolderParams(BaseModel):
    folder_id: str | None = None  # defaults to the sandbox folder from settings


def parse_listing(text: str) -> tuple[list[dict[str, Any]], bool]:
    """Parse a listFolder reply into items and a 'more items available' flag."""
    items = [
        {"id": item_id, "name": name, "is_folder": icon == _FOLDER_ICON}
        for icon, name, item_id in _LISTED_ITEM.findall(text)
    ]
    return items, "More items available" in text


def drive_file_url(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"


class DriveAdapter(ToolAdapter):
    name: ClassVar[str] = "drive"
    actions: ClassVar[dict[str, ActionSpec]] = {
        "create_text_file": ActionSpec(
            name="create_text_file",
            description=(
                "Create a text or Markdown file (name must end in .txt or .md) in a "
                "Google Drive folder."
            ),
            params_model=CreateTextFileParams,
            reversible=True,
        ),
        "list_folder": ActionSpec(
            name="list_folder",
            description="List the files and folders in a Google Drive folder.",
            params_model=ListFolderParams,
            reversible=False,
        ),
    }

    def __init__(self, mcp: McpCaller, default_folder_id: str) -> None:
        self._mcp = mcp
        self._default_folder = default_folder_id

    async def _run(self, action: str, params: BaseModel) -> ToolResult:
        if action == "create_text_file" and isinstance(params, CreateTextFileParams):
            return await self._create_text_file(params)
        if action == "list_folder" and isinstance(params, ListFolderParams):
            return await self._list_folder(params)
        # execute() validates names and params, so reaching here means an adapter bug.
        raise ToolContractError(
            f"drive.{action} received unexpected params {type(params)}"
        )

    async def _create_text_file(self, params: CreateTextFileParams) -> ToolResult:
        folder = params.folder_id or self._default_folder
        text = await self._mcp.call_tool(
            DRIVE_SERVER,
            CREATE_TEXT_FILE_TOOL,
            {"name": params.name, "content": params.content, "parentFolderId": folder},
        )
        file_id = await self._created_file_id(text, params.name, folder)
        return ToolResult(
            summary=f"Created Drive file '{params.name}'",
            output={"file_id": file_id, "name": params.name},
            url=drive_file_url(file_id),
            undo_handle=UndoHandle(
                tool=self.name, action="create_text_file", data={"file_id": file_id}
            ),
        )

    async def _created_file_id(self, reply: str, name: str, folder: str) -> str:
        match = _CREATED_ID.search(reply)
        if match and match.group(1).lower() != "unknown":
            return match.group(1)
        # The server can answer "ID: unknown"; the file exists, so look it up by name.
        listing = await self._mcp.call_tool(
            DRIVE_SERVER, LIST_FOLDER_TOOL, {"folderId": folder}
        )
        items, _ = parse_listing(listing)
        for item in items:
            if item["name"] == name and not item["is_folder"]:
                return str(item["id"])
        raise FatalToolError(
            f"The file '{name}' was created but its id could not be determined; "
            "delete it by hand in your Drive folder."
        )

    async def _list_folder(self, params: ListFolderParams) -> ToolResult:
        folder = params.folder_id or self._default_folder
        text = await self._mcp.call_tool(
            DRIVE_SERVER, LIST_FOLDER_TOOL, {"folderId": folder}
        )
        items, more = parse_listing(text)
        return ToolResult(
            summary=f"{len(items)} item(s) in the folder"
            + (" (more not shown)" if more else ""),
            output={"items": items, "more_available": more},
        )

    async def _undo(self, handle: UndoHandle) -> None:
        file_id = handle.data.get("file_id")
        if not file_id:
            raise FatalToolError(
                "Undo handle for drive.create_text_file has no file_id"
            )
        # Move to trash (restorable). Tool errors propagate as Transient/FatalToolError.
        await self._mcp.call_tool(DRIVE_SERVER, DELETE_ITEM_TOOL, {"itemId": file_id})
