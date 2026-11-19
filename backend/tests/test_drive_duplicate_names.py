"""The bug that a real run exposed, reproduced and fixed: the same title run twice.

A fake Drive server behaves like the real one where it matters: it refuses a second file with a
name that already exists in the folder. The real DriveAdapter and the real executor run against it.
"""

from typing import Any

from app.agent.nodes.execute_step import StepContext, run_step
from app.agent.state import PlanStep
from app.events import EventBus
from app.tools.base import FatalToolError, TransientToolError
from app.tools.drive import DriveAdapter
from app.tools.registry import ToolRegistry

FILE_ICON = "\N{PAGE FACING UP}"


class DuplicateRefusingDrive:
    """createTextFile refuses an existing name; listFolder shows what is stored."""

    def __init__(self, lose_first_reply: bool = False) -> None:
        self.files: dict[str, str] = {}  # name -> id
        self.lose_first_reply = lose_first_reply
        self.create_calls = 0

    async def call_tool(
        self, server: str, tool: str, arguments: dict[str, Any] | None = None
    ) -> str:
        args = arguments or {}
        if tool == "createTextFile":
            self.create_calls += 1
            name = args["name"]
            if name in self.files:
                raise FatalToolError(
                    "drive.createTextFile returned an error: Error: A file named "
                    f'"{name}" already exists in this location.'
                )
            self.files[name] = f"file-{len(self.files) + 1}"
            if self.lose_first_reply and self.create_calls == 1:
                # The file was made, but the answer never reached us.
                raise TransientToolError(
                    "timed out waiting for Drive", outcome_unknown=True
                )
            return f"Created file: {name}\nID: {self.files[name]}"
        if tool == "listFolder":
            lines = [f"{FILE_ICON} {n} (ID: {i})" for n, i in self.files.items()]
            return "Contents of folder:\n\n" + "\n".join(lines)
        raise AssertionError(f"unexpected call {tool}")


def context(drive: DuplicateRefusingDrive, run_id: str) -> StepContext:
    async def no_sleep(seconds: float) -> None:
        return None

    return StepContext(
        registry=ToolRegistry([DriveAdapter(drive, "sandbox")]),
        bus=EventBus(),
        run_id=run_id,
        inputs={},
        injection=None,
        sleep=no_sleep,
        backoff=float,
    )


def step() -> PlanStep:
    return PlanStep(
        id="s2",
        tool="drive",
        action="create_text_file",
        params={"name": "Project Falcon weekly sync.md", "content": "notes"},
    )


async def test_the_same_title_run_twice_now_succeeds_both_times() -> None:
    drive = DuplicateRefusingDrive()
    first = await run_step(step(), 2, context(drive, "a08a70c87e3c"))
    second = await run_step(step(), 2, context(drive, "f91a0896adcd"))
    assert first.ok and second.ok
    assert sorted(drive.files) == [
        "Project Falcon weekly sync (a08a70c8).md",
        "Project Falcon weekly sync (f91a0896).md",
    ]
    assert first.completed is not None and second.completed is not None
    assert first.completed.url != second.completed.url  # each run links its own file
    assert "Project Falcon weekly sync (a08a70c8).md" in (first.record.summary or "")


async def test_the_same_run_id_twice_is_still_one_file_not_two() -> None:
    """A repeat of the very same run (the same token) finds its file instead of failing."""
    drive = DuplicateRefusingDrive()
    first = await run_step(step(), 2, context(drive, "a08a70c87e3c"))
    again = await run_step(step(), 2, context(drive, "a08a70c87e3c"))
    assert first.ok and again.ok
    assert len(drive.files) == 1
    assert again.completed is not None
    assert again.completed.url == first.completed.url  # type: ignore[union-attr]


async def test_a_lost_reply_is_retried_and_adopted_without_a_duplicate() -> None:
    drive = DuplicateRefusingDrive(lose_first_reply=True)
    outcome = await run_step(step(), 2, context(drive, "a08a70c87e3c"))
    assert outcome.ok
    assert (
        outcome.record.attempts == 2
    )  # the first attempt's reply was lost, the second adopted
    assert len(drive.files) == 1  # no duplicate file
    assert outcome.completed is not None and outcome.completed.undo_handle is not None
    assert "from an earlier attempt" in (outcome.record.summary or "")


async def test_without_the_token_the_old_failure_is_reproduced() -> None:
    """The harness reproduces the bug: with plain names, the second run fails exactly as it did live."""
    drive = DuplicateRefusingDrive()
    adapter = DriveAdapter(drive, "sandbox")
    plain = {"name": "Project Falcon weekly sync.md", "content": "notes"}
    await adapter.execute("create_text_file", plain)
    try:
        await adapter.execute("create_text_file", plain)
    except FatalToolError as error:
        assert "already exists" in str(error)
    else:
        raise AssertionError("the second plain create should have been refused")
