"""Live check of the Drive unique-name fix, Drive only (no LLM, Notion, Slack or Langfuse).

Run from the backend/ folder:  python -m scripts.smoke_drive_unique --live

WRITES to your Drive sandbox folder, which is why it refuses to run without --live. It uses the
real executor and the real DriveAdapter against the real Drive MCP server and checks, with one
synthetic title used by two "runs":
  1. run A creates the file and run B creates the SAME title: both succeed, with different names
     (before the fix, the second one failed with "already exists");
  2. both files are listed in the folder;
  3. repeating run A's step (the same run token, like a retry after a lost reply) adopts run A's
     file, with the real server's "already exists" wording, instead of failing or making a third file;
  4. the undo handles move every file it made to the trash, and they are no longer listed.
Whatever happens, it tries to trash what it created and prints names and ids if that fails.
Needs Docker Desktop, the built Drive image and the Drive login (see smoke_drive.py). Never prints
tokens or file contents. Exits non-zero if any check fails.
"""

import argparse
import asyncio
import sys
from datetime import datetime, timezone

from app.agent.nodes.execute_step import StepContext, StepOutcome, run_step
from app.agent.state import CompletedAction, PlanStep
from app.config import Settings, get_settings
from app.events import EventBus
from app.tools.base import ToolError
from app.tools.drive import DriveAdapter
from app.tools.mcp_servers import default_specs
from app.tools.mcp_session import McpSessionManager
from app.tools.registry import ToolRegistry

# Two different run ids; the executor derives each run's token from its id.
RUN_A = "smokea1b2c3d4"
RUN_B = "smokeb5e6f7a8"


def report(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    return ok


def synthetic_title() -> str:
    # timezone.utc, not datetime.UTC: the latter needs Python 3.11 and the brief allows 3.10.
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H-%M-%S")  # noqa: UP017
    return f"WorkflowWeaver unique-name check {stamp} UTC"


def make_step(title: str) -> PlanStep:
    return PlanStep(
        id="s2",
        tool="drive",
        action="create_text_file",
        params={
            "name": f"{title}.md",
            "content": "Synthetic check file. Safe to delete.",
        },
    )


def context(registry: ToolRegistry, run_id: str) -> StepContext:
    return StepContext(
        registry=registry,
        bus=EventBus(),
        run_id=run_id,
        inputs={},
        injection=None,
        sleep=asyncio.sleep,
        backoff=lambda n: 1.0,
    )


def file_name(outcome: StepOutcome) -> str:
    assert outcome.completed is not None
    return str(outcome.completed.summary)


def listed_ids(output: dict) -> set[str]:
    return {str(item["id"]) for item in output["items"]}


async def run(settings: Settings) -> bool:
    ok = True
    title = synthetic_title()
    made: list[CompletedAction] = []  # everything we must trash at the end
    async with McpSessionManager(default_specs(settings)) as mcp:
        registry = ToolRegistry([DriveAdapter(mcp, settings.google_drive_folder_id)])
        try:
            a = await run_step(make_step(title), 2, context(registry, RUN_A))
            ok &= report(
                "run A creates the file",
                a.ok,
                file_name(a) if a.ok else str(a.record.error),
            )
            if a.completed:
                made.append(a.completed)

            b = await run_step(make_step(title), 2, context(registry, RUN_B))
            ok &= report(
                "run B creates the SAME title (this failed before the fix)",
                b.ok,
                file_name(b) if b.ok else str(b.record.error),
            )
            if b.completed:
                made.append(b.completed)
            if not (a.ok and b.ok):
                return False

            assert a.completed and b.completed and a.completed.url and b.completed.url
            ok &= report(
                "the two files have different names and links",
                file_name(a) != file_name(b) and a.completed.url != b.completed.url,
            )

            listing = await registry.execute("drive", "list_folder", {})
            ids = listed_ids(listing.output)
            id_a = str(
                (a.completed.undo_handle.data if a.completed.undo_handle else {})[
                    "file_id"
                ]
            )
            id_b = str(
                (b.completed.undo_handle.data if b.completed.undo_handle else {})[
                    "file_id"
                ]
            )
            ok &= report("both files are listed in the folder", {id_a, id_b} <= ids)

            again = await run_step(make_step(title), 2, context(registry, RUN_A))
            adopted_id = (
                str(again.completed.undo_handle.data["file_id"])
                if again.completed and again.completed.undo_handle
                else ""
            )
            ok &= report(
                "repeating run A's step adopts its file (the real server's 'already exists')",
                again.ok
                and adopted_id == id_a
                and "earlier attempt" in file_name(again),
                file_name(again) if again.ok else str(again.record.error),
            )
            after_repeat = listed_ids(
                (await registry.execute("drive", "list_folder", {})).output
            )
            ok &= report(
                "no third file was created",
                after_repeat - ids == set(),
                f"{len(after_repeat - ids)} new item(s) since the listing",
            )
        except ToolError as error:
            ok = report("live check", False, f"{type(error).__name__}: {error}")
        finally:
            trashed: list[str] = []
            for action in made:
                try:
                    if action.undo_handle:
                        await registry.undo(action.undo_handle)
                        trashed.append(str(action.undo_handle.data["file_id"]))
                except ToolError as error:
                    ok = report(
                        "cleanup",
                        False,
                        f"delete '{action.summary}' ({action.undo_handle}) by hand. "
                        f"{type(error).__name__}: {error}",
                    )
            if trashed:
                final = listed_ids(
                    (await registry.execute("drive", "list_folder", {})).output
                )
                ok &= report(
                    "undo moved every created file to the trash",
                    not (set(trashed) & final),
                    f"{len(trashed)} file(s) trashed",
                )
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Live check of Drive unique file names."
    )
    parser.add_argument(
        "--live", action="store_true", help="required: acknowledges real writes"
    )
    if not parser.parse_args(argv).live:
        parser.error(
            "refusing to run without --live: this creates two real files in your Drive "
            "folder (and moves them to the trash)."
        )
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    return 0 if asyncio.run(run(get_settings())) else 1


if __name__ == "__main__":
    sys.exit(main())
