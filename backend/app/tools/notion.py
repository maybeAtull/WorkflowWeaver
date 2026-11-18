"""Notion adapter: create a page (reversible) and read a page back.

Talks to Notion only through the MCP session manager, using three whitelisted tools.
Undo of a created page is "move to trash" (restorable): the Notion API has no hard delete.
"""

import json
import re
from typing import Annotated, Any, ClassVar, Protocol

from pydantic import BaseModel, Field

from app.tools.base import (
    ActionSpec,
    FatalToolError,
    ToolAdapter,
    ToolContractError,
    ToolResult,
    UndoHandle,
)
from app.tools.mcp_servers import NOTION_SERVER

CREATE_PAGE_TOOL = "API-post-page"
UPDATE_PAGE_TOOL = "API-patch-page"  # in_trash=true is the undo for a created page
RETRIEVE_PAGE_TOOL = "API-retrieve-a-page"

# Notion's own limits: 2000 characters per rich-text block, 100 child blocks per request.
MAX_BLOCK_TEXT = 2000
MAX_BLOCKS = 100


class McpCaller(Protocol):
    """What the adapter needs from the session manager (lets tests pass a fake)."""

    async def call_tool(
        self, server: str, tool: str, arguments: dict[str, Any] | None = None
    ) -> str: ...


ActionItem = Annotated[str, Field(min_length=1, max_length=500)]


class CreatePageParams(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(
        default="", max_length=10_000
    )  # paragraphs split on blank lines
    action_items: list[ActionItem] = Field(default_factory=list, max_length=50)
    parent_page_id: str | None = None  # defaults to the sandbox page from settings


class GetPageParams(BaseModel):
    page_id: str = Field(min_length=1)


def _rich_text(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": {"content": text}}]


def _paragraph_chunks(content: str) -> list[str]:
    chunks: list[str] = []
    for paragraph in re.split(r"\n\s*\n", content.strip()):
        paragraph = paragraph.strip()
        for start in range(0, len(paragraph), MAX_BLOCK_TEXT):
            chunks.append(paragraph[start : start + MAX_BLOCK_TEXT])
    return chunks


def build_children(params: CreatePageParams) -> list[dict[str, Any]]:
    """Turn the body text and action items into Notion blocks."""
    blocks: list[dict[str, Any]] = [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": _rich_text(chunk)},
        }
        for chunk in _paragraph_chunks(params.content)
    ]
    blocks += [
        {
            "object": "block",
            "type": "to_do",
            "to_do": {"rich_text": _rich_text(item), "checked": False},
        }
        for item in params.action_items
    ]
    if len(blocks) > MAX_BLOCKS:
        raise FatalToolError(
            f"Page would need {len(blocks)} blocks; Notion allows {MAX_BLOCKS} per request. "
            "Shorten the content or use fewer action items."
        )
    return blocks


def _parse_page(text: str, *, may_exist: bool) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if not isinstance(data, dict) or not data.get("id"):
        hint = " The page may exist; check Notion by hand." if may_exist else ""
        raise FatalToolError(f"Notion returned an unexpected response.{hint}")
    return data


def _page_title(page: dict[str, Any]) -> str:
    for prop in (page.get("properties") or {}).values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            return "".join(part.get("plain_text", "") for part in prop.get("title", []))
    return ""


class NotionAdapter(ToolAdapter):
    name: ClassVar[str] = "notion"
    actions: ClassVar[dict[str, ActionSpec]] = {
        "create_page": ActionSpec(
            name="create_page",
            description=(
                "Create a Notion page with a title, optional body text and optional "
                "checkbox action items."
            ),
            params_model=CreatePageParams,
            reversible=True,
            # repeat_safe stays False: Notion has no duplicate guard, so repeating a create
            # after an unknown outcome could make a second page.
            planner_hidden_params=frozenset({"parent_page_id"}),  # comes from settings
        ),
        "get_page": ActionSpec(
            name="get_page",
            description="Read a Notion page back: its title, link and whether it is in the trash.",
            params_model=GetPageParams,
            reversible=False,
            repeat_safe=True,  # a read
            planner_visible=False,  # used by the verify step, not for planning
        ),
    }

    def __init__(self, mcp: McpCaller, default_parent_page_id: str) -> None:
        self._mcp = mcp
        self._default_parent = default_parent_page_id

    async def _run(self, action: str, params: BaseModel) -> ToolResult:
        if action == "create_page" and isinstance(params, CreatePageParams):
            return await self._create_page(params)
        if action == "get_page" and isinstance(params, GetPageParams):
            return await self._get_page(params)
        # execute() validates names and params, so reaching here means an adapter bug.
        raise ToolContractError(
            f"notion.{action} received unexpected params {type(params)}"
        )

    async def _create_page(self, params: CreatePageParams) -> ToolResult:
        arguments = {
            "parent": {"page_id": params.parent_page_id or self._default_parent},
            "properties": {"title": _rich_text(params.title)},
            "children": build_children(params),
        }
        text = await self._mcp.call_tool(NOTION_SERVER, CREATE_PAGE_TOOL, arguments)
        page = _parse_page(text, may_exist=True)
        page_id = str(page["id"])
        return ToolResult(
            summary=f"Created Notion page '{params.title}'",
            output={"page_id": page_id, "title": params.title},
            url=page.get("url"),
            undo_handle=UndoHandle(
                tool=self.name, action="create_page", data={"page_id": page_id}
            ),
        )

    async def _get_page(self, params: GetPageParams) -> ToolResult:
        text = await self._mcp.call_tool(
            NOTION_SERVER, RETRIEVE_PAGE_TOOL, {"page_id": params.page_id}
        )
        page = _parse_page(text, may_exist=False)
        title = _page_title(page)
        in_trash = bool(page.get("in_trash") or page.get("archived"))
        return ToolResult(
            summary=f"Notion page '{title}'" + (" (in trash)" if in_trash else ""),
            output={"page_id": str(page["id"]), "title": title, "in_trash": in_trash},
            url=page.get("url"),
        )

    async def _undo(self, handle: UndoHandle) -> None:
        page_id = handle.data.get("page_id")
        if not page_id:
            raise FatalToolError("Undo handle for notion.create_page has no page_id")
        # Move to trash (restorable). Tool errors propagate as Transient/FatalToolError.
        await self._mcp.call_tool(
            NOTION_SERVER, UPDATE_PAGE_TOOL, {"page_id": page_id, "in_trash": True}
        )
