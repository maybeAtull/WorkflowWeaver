"""Smoke test for the Google Drive MCP server.

Run from the backend/ folder:
  python -m scripts.smoke_drive            stage A: read-only (list tools, list the Drive folder)
  python -m scripts.smoke_drive --write    also stage B: create a small text file in the folder,
                                           verify it, then move it to trash (create + undo path);
                                           if a step fails after the file exists it tries to
                                           trash it anyway and prints its name and id

Prerequisites: build the image once (docker build -t workflowweaver-drive-mcp docker/drive-mcp)
and complete the one-time login (npx -y @piotr-agier/google-drive-mcp@2.13.0 auth), which saves
tokens.json. Containers cannot open a browser, so the login happens on the host.

Never prints tokens or file contents. Exits non-zero if any step fails.
"""

import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import ENV_FILE

DRIVE_IMAGE = "workflowweaver-drive-mcp"
STEP_TIMEOUT_SECONDS = 120
# Narrowest scopes that cover what we do: Drive files and Google Docs. The server's default
# also requests Sheets, Slides and Calendar, which we do not need.
DRIVE_SCOPES = "drive,documents"
# Where the host-side login saves tokens.json by default (see the Dockerfile for why we
# mount the directory and not the single file).
HOST_TOKEN_DIR = Path.home() / ".config" / "google-drive-mcp"

LIST_FOLDER_TOOL = "listFolder"
CREATE_TEXT_FILE_TOOL = "createTextFile"
DELETE_ITEM_TOOL = "deleteItem"  # moves to trash (restorable): our undo


class DriveSettings(BaseSettings):
    """Only what this script needs, so a Drive test doesn't demand every other credential."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )
    google_credentials_path: Path
    google_drive_folder_id: str


def report(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    return ok


def explain_error(exc: Exception) -> str:
    """Plain-language hint for common failures."""
    message = str(exc)
    lowered = message.lower()
    if "invalid_grant" in lowered or re.search(r"\b401\b", lowered):
        hint = (
            "login expired or revoked (apps in Testing mode expire after 7 days); "
            "re-run the one-time login"
        )
    elif "accessnotconfigured" in lowered or "has not been used" in lowered:
        hint = "the Drive or Docs API is not enabled in your Google Cloud project"
    elif "insufficient" in lowered or re.search(r"\b403\b", lowered):
        hint = "missing permission or scope; re-run the login with the drive and documents scopes"
    elif re.search(r"\b404\b", lowered) or "not found" in lowered:
        hint = "folder not found; check GOOGLE_DRIVE_FOLDER_ID and that this account can see it"
    elif "docker" in lowered or "pipe" in lowered:
        hint = "Docker problem; is Docker Desktop running, and is the image built?"
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


def resolve_credentials(path: Path) -> Path:
    # Relative paths in .env are relative to the repo root, not to where the script runs.
    return path if path.is_absolute() else (ENV_FILE.parent / path).resolve()


def build_client(credentials: Path) -> MultiServerMCPClient:
    # Credentials are mounted read-only; the token directory is read-write because the server
    # refreshes tokens in place. Nothing secret is passed on the command line.
    return MultiServerMCPClient(
        {
            "drive": {
                "transport": "stdio",
                "command": "docker",
                "args": [
                    "run",
                    "--rm",
                    "-i",
                    "-v",
                    f"{credentials.as_posix()}:/config/gcp-oauth.keys.json:ro",
                    "-v",
                    f"{HOST_TOKEN_DIR.as_posix()}:/config/tokens",
                    "-e",
                    "GOOGLE_DRIVE_MCP_SCOPES",
                    DRIVE_IMAGE,
                ],
                "env": {"GOOGLE_DRIVE_MCP_SCOPES": DRIVE_SCOPES},
            }
        },
        handle_tool_errors=False,  # raise real tool errors so we can classify them
    )


def folder_id_field(tool: BaseTool) -> str | None:
    properties = schema_properties(tool)
    return next(
        (p for p in properties if "folder" in p.lower() and "id" in p.lower()), None
    )


def result_text(result: object) -> str:
    """Flatten an MCP tool result (a string or a list of content blocks) to plain text."""
    if isinstance(result, list):
        return "".join(
            str(block.get("text", "")) if isinstance(block, dict) else str(block)
            for block in result
        )
    return str(result)


def _walk(value: object):
    """Yield every dict nested anywhere inside a parsed JSON value."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def extract_file_id(text: str, name: str | None = None) -> str | None:
    """Find a Drive file id in a tool response (JSON, or plain text containing an id).

    With a name, prefer the id of the item that has that name (used on folder listings).
    """
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if data is not None:
        for item in _walk(data):
            item_id = item.get("id") or item.get("fileId")
            if item_id and (name is None or item.get("name") == name):
                return str(item_id)
    if name is None:
        match = re.search(r"\b(?:id|ID|Id)\W{0,4}([A-Za-z0-9_-]{20,})", text)
        if match:
            return match.group(1)
    return None


async def call(tool: BaseTool, args: dict) -> object:
    return await asyncio.wait_for(tool.ainvoke(args), STEP_TIMEOUT_SECONDS)


async def write_round_trip(
    by_name: dict[str, BaseTool], settings: DriveSettings, folder_field: str
) -> bool:
    """Create a text file, verify it, trash it, verify the trash. Cleans up on failure."""
    # timezone.utc rather than datetime.UTC: the latter needs Python 3.11, the brief allows 3.10.
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H-%M-%S")  # noqa: UP017
    name = f"WorkflowWeaver smoke test {stamp} UTC.txt"  # synthetic, never real data
    folder = settings.google_drive_folder_id
    list_tool = by_name[LIST_FOLDER_TOOL]
    delete_tool = by_name[DELETE_ITEM_TOOL]
    file_id: str | None = None
    trashed = False
    ok = True
    try:
        created = result_text(
            await call(
                by_name[CREATE_TEXT_FILE_TOOL],
                {
                    "name": name,
                    "content": "Synthetic smoke-test file. Safe to delete.",
                    "parentFolderId": folder,
                },
            )
        )
        listing = result_text(await call(list_tool, {folder_field: folder}))
        file_id = extract_file_id(created) or extract_file_id(listing, name)
        if file_id is None:
            return report(
                "create file",
                False,
                f"file was created but its id could not be found; delete '{name}' by hand "
                f"in your Drive folder. Create response started with: {created[:200]!r}",
            )
        report("create file", True, f"id={file_id}")
        ok &= report("verify file listed", name in listing)

        await call(delete_tool, {"itemId": file_id})
        trashed = True
        after = result_text(await call(list_tool, {folder_field: folder}))
        ok &= report("undo: file moved to trash", name not in after)
    except Exception as exc:  # noqa: BLE001
        ok = report("write round trip", False, explain_error(exc))
    finally:
        if file_id and not trashed:
            try:
                await call(delete_tool, {"itemId": file_id})
                report("cleanup after failure", True, f"trashed {file_id}")
            except Exception as exc:  # noqa: BLE001
                ok = report(
                    "cleanup after failure",
                    False,
                    f"delete '{name}' (id {file_id}) by hand. {explain_error(exc)}",
                )
    return ok


async def run(settings: DriveSettings, write: bool) -> bool:
    credentials = resolve_credentials(settings.google_credentials_path)
    token_file = HOST_TOKEN_DIR / "tokens.json"
    files_ok = credentials.is_file() and token_file.is_file()
    if not report(
        "files present",
        files_ok,
        f"credentials={credentials.is_file()}, tokens.json={token_file.is_file()}"
        + ("" if files_ok else " (run the one-time login first)"),
    ):
        return False

    client = build_client(credentials)
    try:
        tools = await asyncio.wait_for(client.get_tools(), STEP_TIMEOUT_SECONDS)
    # Any startup failure is a smoke-test failure, so catching broadly is intended here.
    except Exception as exc:  # noqa: BLE001
        return report("start server and list tools", False, explain_error(exc))
    by_name = {tool.name: tool for tool in tools}
    needed = (LIST_FOLDER_TOOL, CREATE_TEXT_FILE_TOOL, DELETE_ITEM_TOOL)
    missing = [name for name in needed if name not in by_name]
    if not report(
        "start server and list tools",
        not missing,
        f"{len(tools)} tools"
        + (
            f", MISSING needed tools: {missing}"
            if missing
            else ", all needed tools present"
        ),
    ):
        return False
    # Parameter names for the tools stage B will use (names only, no values).
    for name in needed:
        print(f"       {name} parameters: {list(schema_properties(by_name[name]))}")

    list_tool = by_name[LIST_FOLDER_TOOL]
    field = folder_id_field(list_tool)
    if field is None:
        return report(
            "list Drive folder",
            False,
            f"no folder-id parameter on {LIST_FOLDER_TOOL}: {list(schema_properties(list_tool))}",
        )
    try:
        result = await asyncio.wait_for(
            list_tool.ainvoke({field: settings.google_drive_folder_id}),
            STEP_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        return report("list Drive folder", False, explain_error(exc))
    list_ok = report(
        "list Drive folder",
        True,
        f"tool={LIST_FOLDER_TOOL}, response of {len(str(result))} characters (contents not printed)",
    )
    if not write:
        return list_ok
    return await write_round_trip(by_name, settings, field)


def main() -> int:
    try:
        settings = DriveSettings()
    except ValidationError as exc:
        # Field names and messages only; never the offending input values.
        problems = [
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        ]
        report("config loads", False, "; ".join(problems))
        return 1
    report(
        "config loads",
        True,
        "GOOGLE_CREDENTIALS_PATH and GOOGLE_DRIVE_FOLDER_ID are set",
    )
    write = "--write" in sys.argv[1:]
    return 0 if asyncio.run(run(settings, write)) else 1


if __name__ == "__main__":
    sys.exit(main())
