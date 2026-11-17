"""Smoke test for the Notion MCP server.

Run from the backend/ folder:
  python -m scripts.smoke_notion            stage 1: read-only (list tools, read the sandbox page)
  python -m scripts.smoke_notion --write    also stage 2: create a child page, verify it, then
                                            move it to trash (proves the create + undo path)

Starts the official Notion MCP server in Docker (stdio). Stage 1 changes nothing in Notion.
Stage 2 creates one synthetic test page under NOTION_PAGE_ID and trashes it again; if a step
fails after the page exists it tries to trash it anyway and prints the page id.
Never prints the token. Exits non-zero if any step fails.
"""

import asyncio
import json
import re
import sys
from datetime import datetime, timezone

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import ENV_FILE

NOTION_IMAGE = "mcp/notion"
STEP_TIMEOUT_SECONDS = 120

# Tool names exposed by the official server (discovered in stage 1).
CREATE_PAGE_TOOL = "API-post-page"
UPDATE_PAGE_TOOL = "API-patch-page"  # in_trash=true is the undo for a created page
RETRIEVE_PAGE_TOOL = "API-retrieve-a-page"


class NotionSettings(BaseSettings):
    """Only what this script needs, so a Notion test doesn't demand every other credential."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )
    notion_token: SecretStr
    notion_page_id: str


def report(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    return ok


def normalise_id(value: str) -> str:
    return value.replace("-", "").lower()


def explain_error(exc: Exception, token: str) -> str:
    """Plain-language hint for common failures, with the token redacted."""
    message = str(exc).replace(token, "***") if token else str(exc)
    lowered = message.lower()
    if re.search(r"\b401\b", lowered) or "unauthorized" in lowered:
        hint = "token rejected; check NOTION_TOKEN"
    elif re.search(r"\b404\b", lowered) or "object_not_found" in lowered:
        hint = (
            "page not found; share the page with your integration "
            "(page menu > Connections) and check NOTION_PAGE_ID"
        )
    elif re.search(r"\b403\b", lowered) or "restricted_resource" in lowered:
        hint = (
            "integration lacks permission; enable the content capabilities "
            "(read, insert, update) for the integration"
        )
    elif "docker" in lowered or "pipe" in lowered:
        hint = "Docker problem; is Docker Desktop running and resumed?"
    else:
        hint = "unexpected error"
    return f"{type(exc).__name__} ({hint}): {message[:1000]}"


def schema_properties(tool: BaseTool) -> dict[str, object]:
    """MCP tools carry a JSON-schema dict; fall back to a pydantic model if needed."""
    schema = tool.args_schema
    if isinstance(schema, dict):
        return dict(schema.get("properties", {}))
    if schema is not None:
        return dict(schema.model_json_schema().get("properties", {}))
    return {}


def find_retrieve_page_tool(tools: list[BaseTool]) -> BaseTool | None:
    for tool in tools:
        name = tool.name.lower()
        if "retrieve" in name and "page" in name and "propert" not in name:
            return tool
    return None


def result_text(result: object) -> str:
    """Flatten an MCP tool result (a string or a list of content blocks) to plain text."""
    if isinstance(result, list):
        return "".join(
            str(block.get("text", "")) if isinstance(block, dict) else str(block)
            for block in result
        )
    return str(result)


def parse_page(result: object) -> dict:
    """Parse a Notion page object from a tool result; empty dict if it isn't JSON."""
    try:
        data = json.loads(result_text(result))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


async def call(tool: BaseTool, args: dict) -> object:
    return await asyncio.wait_for(tool.ainvoke(args), STEP_TIMEOUT_SECONDS)


async def write_round_trip(
    tools: list[BaseTool], settings: NotionSettings, token: str
) -> bool:
    """Create a child page, verify it, trash it, verify the trash. Cleans up on failure."""
    by_name = {tool.name: tool for tool in tools}
    wanted = (CREATE_PAGE_TOOL, UPDATE_PAGE_TOOL, RETRIEVE_PAGE_TOOL)
    missing = [name for name in wanted if name not in by_name]
    if missing:
        return report("write round trip", False, f"missing tools: {missing}")

    # timezone.utc rather than datetime.UTC: the latter needs Python 3.11, the brief allows 3.10.
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")  # noqa: UP017
    title = f"WorkflowWeaver smoke test {stamp} UTC"  # synthetic, never real data
    page_id: str | None = None
    trashed = False
    ok = True
    try:
        created = parse_page(
            await call(
                by_name[CREATE_PAGE_TOOL],
                {
                    "parent": {"page_id": settings.notion_page_id},
                    "properties": {"title": [{"text": {"content": title}}]},
                },
            )
        )
        page_id = created.get("id")
        if not page_id:
            return report("create page", False, "response did not contain a page id")
        report("create page", True, f"id={page_id}")

        fetched = parse_page(
            await call(by_name[RETRIEVE_PAGE_TOOL], {"page_id": page_id})
        )
        ok &= report(
            "verify page exists",
            normalise_id(str(fetched.get("id", ""))) == normalise_id(page_id),
        )

        await call(by_name[UPDATE_PAGE_TOOL], {"page_id": page_id, "in_trash": True})
        trashed = True
        after = parse_page(
            await call(by_name[RETRIEVE_PAGE_TOOL], {"page_id": page_id})
        )
        in_trash = bool(after.get("in_trash") or after.get("archived"))
        ok &= report("undo: page moved to trash", in_trash, f"in_trash={in_trash}")
    except Exception as exc:  # noqa: BLE001
        ok = report("write round trip", False, explain_error(exc, token))
    finally:
        if page_id and not trashed:
            try:
                await call(
                    by_name[UPDATE_PAGE_TOOL], {"page_id": page_id, "in_trash": True}
                )
                report("cleanup after failure", True, f"trashed {page_id}")
            except Exception as exc:  # noqa: BLE001
                ok = report(
                    "cleanup after failure",
                    False,
                    f"delete page {page_id} by hand in Notion. {explain_error(exc, token)}",
                )
    return ok


async def run(settings: NotionSettings, write: bool) -> bool:
    token = settings.notion_token.get_secret_value()
    # The token goes in the child process environment, never on the command line, so it
    # cannot show up in a process list. "-e NOTION_TOKEN" tells Docker to pass it through.
    client = MultiServerMCPClient(
        {
            "notion": {
                "transport": "stdio",
                "command": "docker",
                "args": ["run", "--rm", "-i", "-e", "NOTION_TOKEN", NOTION_IMAGE],
                "env": {"NOTION_TOKEN": token},
            }
        },
        handle_tool_errors=False,  # raise real tool errors so we can classify them
    )

    try:
        tools = await asyncio.wait_for(client.get_tools(), STEP_TIMEOUT_SECONDS)
    # Any startup failure is a smoke-test failure, so catching broadly is intended here.
    except Exception as exc:  # noqa: BLE001
        return report("start server and list tools", False, explain_error(exc, token))
    names = sorted(tool.name for tool in tools)
    report("start server and list tools", True, f"{len(tools)} tools")
    print("       tools: " + ", ".join(names))

    tool = find_retrieve_page_tool(tools)
    if tool is None:
        page_like = [name for name in names if "page" in name.lower()]
        return report(
            "read sandbox page",
            False,
            f"no 'retrieve page' tool found; page-related tools: {page_like}",
        )
    properties = schema_properties(tool)
    id_field = next((p for p in properties if "page_id" in p.lower()), None)
    if id_field is None:
        return report(
            "read sandbox page",
            False,
            f"tool {tool.name} has no page-id parameter; parameters: {list(properties)}",
        )

    try:
        result = await asyncio.wait_for(
            tool.ainvoke({id_field: settings.notion_page_id}), STEP_TIMEOUT_SECONDS
        )
    except Exception as exc:  # noqa: BLE001
        return report("read sandbox page", False, explain_error(exc, token))
    text = normalise_id(str(result))
    found = normalise_id(settings.notion_page_id) in text
    read_ok = report(
        "read sandbox page",
        found,
        f"tool={tool.name}, page id present in response={found}",
    )
    if not read_ok or not write:
        return read_ok
    return await write_round_trip(tools, settings, token)


def main() -> int:
    try:
        settings = NotionSettings()
    except ValidationError as exc:
        # Field names and messages only; never the offending input values.
        problems = [
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        ]
        report("config loads", False, "; ".join(problems))
        return 1
    report("config loads", True, "NOTION_TOKEN and NOTION_PAGE_ID are set")
    write = "--write" in sys.argv[1:]
    return 0 if asyncio.run(run(settings, write)) else 1


if __name__ == "__main__":
    sys.exit(main())
