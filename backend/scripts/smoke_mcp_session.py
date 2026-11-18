"""Smoke test for McpSessionManager against the real Notion and Drive MCP servers.

Run from the backend/ folder:  python -m scripts.smoke_mcp_session

Read-only. Proves, with real services: servers start lazily on first use, are reused (the
second call is much faster than the first), a tool outside the whitelist is refused, and no
containers are left running after the manager closes. Needs Docker Desktop, the built Drive
image and the one-time Drive login (see smoke_drive.py). Never prints tool output or tokens.
Exits non-zero if any check fails.
"""

import asyncio
import subprocess
import sys
import time

from pydantic import ValidationError

from app.config import Settings, get_settings
from app.tools.base import FatalToolError, ToolError
from app.tools.mcp_servers import (
    DRIVE_IMAGE,
    DRIVE_SERVER,
    NOTION_IMAGE,
    NOTION_SERVER,
    default_specs,
)
from app.tools.mcp_session import McpSessionManager


def report(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    return ok


async def timed_call(
    mcp: McpSessionManager, server: str, tool: str, arguments: dict[str, str]
) -> tuple[str, float]:
    started = time.perf_counter()
    text = await mcp.call_tool(server, tool, arguments)
    return text, time.perf_counter() - started


def running_containers(image: str) -> list[str]:
    result = subprocess.run(
        ["docker", "ps", "-q", "--filter", f"ancestor={image}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.split()


async def check_server(
    mcp: McpSessionManager,
    label: str,
    server: str,
    tool: str,
    arguments: dict[str, str],
    expect_in_text: str | None = None,
) -> bool:
    try:
        first_text, first = await timed_call(mcp, server, tool, arguments)
        _, second = await timed_call(mcp, server, tool, arguments)
    except ToolError as exc:
        return report(label, False, f"{type(exc).__name__}: {exc}")
    content_ok = expect_in_text is None or expect_in_text in first_text.replace("-", "")
    return report(
        label,
        content_ok and second < first,
        f"first call {first:.1f}s (includes server start), second {second:.1f}s (reused); "
        f"response {len(first_text)} characters, contents not printed",
    )


async def run(settings: Settings) -> bool:
    ok = True
    async with McpSessionManager(default_specs(settings)) as mcp:
        ok &= await check_server(
            mcp,
            "notion: lazy start, then reuse",
            NOTION_SERVER,
            "API-retrieve-a-page",
            {"page_id": settings.notion_page_id},
            expect_in_text=settings.notion_page_id.replace("-", ""),
        )
        ok &= await check_server(
            mcp,
            "drive: lazy start, then reuse",
            DRIVE_SERVER,
            "listFolder",
            {"folderId": settings.google_drive_folder_id},
        )
        try:
            await mcp.call_tool(NOTION_SERVER, "API-delete-a-block", {"block_id": "x"})
            ok &= report("whitelist blocks other tools", False, "call was NOT refused")
        except FatalToolError as exc:
            ok &= report("whitelist blocks other tools", "whitelist" in str(exc))

    leftover = running_containers(NOTION_IMAGE) + running_containers(DRIVE_IMAGE)
    ok &= report(
        "clean shutdown", not leftover, f"leftover containers: {leftover or 'none'}"
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
