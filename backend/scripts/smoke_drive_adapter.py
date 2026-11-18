"""Smoke test for DriveAdapter against the real Google Drive MCP server.

Run from the backend/ folder:  python -m scripts.smoke_drive_adapter

WRITES to Drive: creates one synthetic text file in GOOGLE_DRIVE_FOLDER_ID, lists the folder to
find it, tries to create the same name again (the server must refuse: its duplicate guard),
undoes the create (moves the file to the trash) and confirms it is gone from the listing.
If a step fails after the file exists it tries to trash it anyway and prints its name and id.
Needs Docker Desktop, the built Drive image and the one-time Drive login (see smoke_drive.py).
Never prints tokens or file contents. Exits non-zero if any check fails.
"""

import asyncio
import sys
from datetime import datetime, timezone

from pydantic import ValidationError

from app.config import Settings, get_settings
from app.tools.base import FatalToolError, ToolError, UndoHandle
from app.tools.drive import DriveAdapter
from app.tools.mcp_servers import default_specs
from app.tools.mcp_session import McpSessionManager


def report(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    return ok


def contains_id(listing_items: list[dict], file_id: str) -> bool:
    return any(item["id"] == file_id for item in listing_items)


async def run(settings: Settings) -> bool:
    # timezone.utc rather than datetime.UTC: the latter needs Python 3.11, the brief allows 3.10.
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H-%M-%S")  # noqa: UP017
    name = f"WorkflowWeaver adapter smoke test {stamp} UTC.txt"  # synthetic, never real data
    handle: UndoHandle | None = None
    undone = False
    ok = True
    async with McpSessionManager(default_specs(settings)) as mcp:
        drive = DriveAdapter(mcp, settings.google_drive_folder_id)
        try:
            created = await drive.execute(
                "create_text_file",
                {"name": name, "content": "Synthetic smoke-test file. Safe to delete."},
            )
            handle = created.undo_handle
            file_id = created.output["file_id"]
            ok &= report(
                "create_text_file",
                handle is not None,
                f"id={file_id}, link={created.url}, undo handle recorded={handle is not None}",
            )

            listed = await drive.execute("list_folder", {})
            ok &= report(
                "list_folder: new file is listed",
                contains_id(listed.output["items"], file_id),
                f"{len(listed.output['items'])} item(s) in the folder",
            )

            try:
                await drive.execute("create_text_file", {"name": name})
                ok &= report(
                    "duplicate name is refused", False, "second create was NOT refused"
                )
            except FatalToolError as exc:
                ok &= report(
                    "duplicate name is refused",
                    "already exists" in str(exc),
                    "the server's duplicate guard stopped it",
                )

            assert handle is not None
            await drive.undo(handle)
            undone = True
            after = await drive.execute("list_folder", {})
            ok &= report(
                "undo: file moved to trash",
                not contains_id(after.output["items"], file_id),
                "no longer listed in the folder",
            )
        except ToolError as exc:
            ok = report("adapter round trip", False, f"{type(exc).__name__}: {exc}")
        finally:
            if handle is not None and not undone:
                try:
                    await drive.undo(handle)
                    report("cleanup after failure", True, f"trashed {handle.data}")
                except ToolError as exc:
                    ok = report(
                        "cleanup after failure",
                        False,
                        f"delete '{name}' ({handle.data}) by hand. {type(exc).__name__}: {exc}",
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
