"""Smoke test for NotionAdapter against the real Notion MCP server.

Run from the backend/ folder:  python -m scripts.smoke_notion_adapter

WRITES to Notion: creates one synthetic page (with a body and two checkbox action items)
under NOTION_PAGE_ID, reads it back, undoes it (moves it to the trash) and confirms the
trash. If a step fails after the page exists it tries to trash it anyway and prints the id.
Needs Docker Desktop. Never prints tokens. Exits non-zero if any check fails.
"""

import asyncio
import sys
from datetime import datetime, timezone

from pydantic import ValidationError

from app.config import Settings, get_settings
from app.tools.base import ToolError, UndoHandle
from app.tools.mcp_servers import default_specs
from app.tools.mcp_session import McpSessionManager
from app.tools.notion import NotionAdapter


def report(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    return ok


async def run(settings: Settings) -> bool:
    # timezone.utc rather than datetime.UTC: the latter needs Python 3.11, the brief allows 3.10.
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")  # noqa: UP017
    title = (
        f"WorkflowWeaver adapter smoke test {stamp} UTC"  # synthetic, never real data
    )
    handle: UndoHandle | None = None
    undone = False
    ok = True
    async with McpSessionManager(default_specs(settings)) as mcp:
        notion = NotionAdapter(mcp, settings.notion_page_id)
        try:
            created = await notion.execute(
                "create_page",
                {
                    "title": title,
                    "content": "Synthetic paragraph one.\n\nSynthetic paragraph two.",
                    "action_items": ["Synthetic task A", "Synthetic task B"],
                },
            )
            handle = created.undo_handle
            page_id = created.output["page_id"]
            ok &= report(
                "create_page",
                handle is not None and created.url is not None,
                f"id={page_id}, link={created.url}, undo handle recorded={handle is not None}",
            )

            fetched = await notion.execute("get_page", {"page_id": page_id})
            ok &= report(
                "get_page: exists and is live",
                fetched.output["title"] == title and not fetched.output["in_trash"],
                f"title matches={fetched.output['title'] == title}, in_trash={fetched.output['in_trash']}",
            )

            assert handle is not None
            await notion.undo(handle)
            undone = True
            after = await notion.execute("get_page", {"page_id": page_id})
            ok &= report(
                "undo: page moved to trash",
                bool(after.output["in_trash"]),
                f"in_trash={after.output['in_trash']}",
            )
        except ToolError as exc:
            ok = report("adapter round trip", False, f"{type(exc).__name__}: {exc}")
        finally:
            if handle is not None and not undone:
                try:
                    await notion.undo(handle)
                    report("cleanup after failure", True, f"trashed {handle.data}")
                except ToolError as exc:
                    ok = report(
                        "cleanup after failure",
                        False,
                        f"delete page {handle.data} by hand. {type(exc).__name__}: {exc}",
                    )
    return ok


def main() -> int:
    try:
        settings = get_settings()
    except ValidationError as exc:
        # Field names and messages only; never the offending input values.
        problems = [
            f"{'.'.join(str(p) for p in e['loc']) or 'settings'}: {e['msg']}"
            for e in exc.errors()
        ]
        report("config loads", False, "; ".join(problems))
        return 1
    report("config loads", True, "all settings valid")
    return 0 if asyncio.run(run(settings)) else 1


if __name__ == "__main__":
    sys.exit(main())
