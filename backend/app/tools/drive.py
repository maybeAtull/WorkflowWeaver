"""Google Drive adapter: create a text file (reversible) and list a folder.

Talks to Drive only through the MCP session manager, using three whitelisted tools. The reply
formats below were read from the pinned server's source (google-drive-mcp 2.13.0), not guessed:
  createTextFile -> "Created file: <name>\\nID: <id>"
  listFolder     -> "Contents of folder:\\n\\n<icon> <name> (ID: <id>)" per item
Undo of a created file is "move to trash" (restorable).

The server refuses to create a file whose name already exists in the folder. That protects a
retried create from making a duplicate, but it also meant a second run with the same title failed.
So the executor gives every run a short token (`unique_token`) and the file is named
"<title> (<token>).md": two runs can never collide, and every attempt of ONE step asks for the
same name. If the server says "already exists" for such a name, the file can only be ours (the
token is the run's), i.e. an earlier attempt created it but its reply was lost, so we adopt it
instead of failing. That keeps a create safe to repeat.
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
    VerifyResult,
)
from app.tools.mcp_servers import DRIVE_SERVER

CREATE_TEXT_FILE_TOOL = "createTextFile"
LIST_FOLDER_TOOL = "listFolder"
DELETE_ITEM_TOOL = "deleteItem"  # moves to trash (restorable): our undo

ALLOWED_EXTENSIONS = (".txt", ".md")  # the server rejects any other text-file name
MAX_CONTENT_CHARS = 50_000
MAX_NAME_CHARS = 200
_ALREADY_EXISTS = "already exists"  # how the server words a duplicate-name refusal

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
    # Set by the executor from the run id, never by the planner. When present it is added to the
    # file name so the name is unique to this run (see the module docstring).
    unique_token: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{1,16}$")

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


def unique_file_name(name: str, token: str) -> str:
    """'Notes.md' + 'a1b2c3d4' -> 'Notes (a1b2c3d4).md'. The extension keeps its case, and the
    title is trimmed (never the token or the extension) so the whole name stays within the limit.
    """
    extension = next(ext for ext in ALLOWED_EXTENSIONS if name.lower().endswith(ext))
    stem = name[: -len(extension)]
    extension = name[len(stem) :]  # as written, e.g. ".MD"
    suffix = f" ({token})"
    room = MAX_NAME_CHARS - len(suffix) - len(extension)
    return f"{stem[:room].strip() or 'file'}{suffix}{extension}"


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
            # Repeating a create cannot duplicate: the server refuses a second file with the
            # same name, and our unique names make that refusal mean "an earlier attempt of
            # this step made it", which is then adopted.
            repeat_safe=True,
            # folder_id comes from settings; unique_token from the run id (the executor).
            planner_hidden_params=frozenset({"folder_id", "unique_token"}),
        ),
        "list_folder": ActionSpec(
            name="list_folder",
            description="List the files and folders in a Google Drive folder.",
            params_model=ListFolderParams,
            reversible=False,
            repeat_safe=True,  # a read
            planner_visible=False,  # used by the verify step, not for planning
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
        name = (
            unique_file_name(params.name, params.unique_token)
            if params.unique_token
            else params.name
        )
        try:
            text = await self._mcp.call_tool(
                DRIVE_SERVER,
                CREATE_TEXT_FILE_TOOL,
                {"name": name, "content": params.content, "parentFolderId": folder},
            )
        except FatalToolError as error:
            # With a unique name, "already exists" can only mean an earlier attempt of this very
            # step created the file and its reply never reached us. Take it as ours.
            if params.unique_token and _ALREADY_EXISTS in str(error).lower():
                existing = await self._find_file(name, folder)
                if existing is not None:
                    return self._created(name, existing, folder, adopted=True)
            raise
        file_id = await self._created_file_id(text, name, folder)
        return self._created(name, file_id, folder)

    @staticmethod
    def _created(
        name: str, file_id: str, folder: str, adopted: bool = False
    ) -> ToolResult:
        output: dict[str, Any] = {"file_id": file_id, "name": name}
        if adopted:
            output["adopted"] = (
                True  # found, not newly made: created by an earlier attempt
            )
        return ToolResult(
            summary=(
                f"Found Drive file '{name}' from an earlier attempt"
                if adopted
                else f"Created Drive file '{name}'"
            ),
            output=output,
            url=drive_file_url(file_id),
            undo_handle=UndoHandle(
                tool="drive",
                action="create_text_file",
                # The folder is kept so verify looks where the file was really created
                # (a step can override the default folder).
                data={"file_id": file_id, "folder_id": folder},
            ),
        )

    async def _find_file(self, name: str, folder: str) -> str | None:
        """The id of the file called exactly `name` in the folder, or None (also when the
        listing is cut off, because then its absence proves nothing)."""
        listing = await self._mcp.call_tool(
            DRIVE_SERVER, LIST_FOLDER_TOOL, {"folderId": folder}
        )
        items, _ = parse_listing(listing)
        for item in items:
            if item["name"] == name and not item["is_folder"]:
                return str(item["id"])
        return None

    async def _created_file_id(self, reply: str, name: str, folder: str) -> str:
        match = _CREATED_ID.search(reply)
        if match and match.group(1).lower() != "unknown":
            return match.group(1)
        # The server can answer "ID: unknown"; the file exists, so look it up by name.
        found = await self._find_file(name, folder)
        if found is not None:
            return found
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

    async def _verify(self, handle: UndoHandle) -> VerifyResult:
        file_id = handle.data.get("file_id")
        if not file_id:
            raise FatalToolError("Handle for drive.create_text_file has no file_id")
        folder = handle.data.get("folder_id") or self._default_folder
        listing = await self._list_folder(ListFolderParams(folder_id=folder))
        if any(
            item["id"] == file_id and not item["is_folder"]
            for item in listing.output["items"]
        ):
            return VerifyResult(status="verified", detail="The file is in the folder.")
        if listing.output["more_available"]:
            # The listing shows only a first page, so absence proves nothing.
            return VerifyResult(
                status="unconfirmed",
                detail="The folder has more items than could be listed.",
            )
        return VerifyResult(status="failed", detail="The file is not in the folder.")

    async def _undo(self, handle: UndoHandle) -> None:
        file_id = handle.data.get("file_id")
        if not file_id:
            raise FatalToolError(
                "Undo handle for drive.create_text_file has no file_id"
            )
        # Move to trash (restorable). Tool errors propagate as Transient/FatalToolError.
        await self._mcp.call_tool(DRIVE_SERVER, DELETE_ITEM_TOOL, {"itemId": file_id})
