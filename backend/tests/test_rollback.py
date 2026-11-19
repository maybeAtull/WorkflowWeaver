from typing import Any, ClassVar

from pydantic import BaseModel

from app.agent.nodes.execute_step import StepContext
from app.agent.nodes.rollback import RollbackResult, run_rollback
from app.agent.state import CompletedAction, RollbackRecord
from app.events import EventBus
from app.tools.base import (
    ActionSpec,
    FatalToolError,
    ToolAdapter,
    ToolResult,
    TransientToolError,
    UndoHandle,
)
from app.tools.registry import ToolRegistry


class Params(BaseModel):
    pass


class FakeAdapter(ToolAdapter):
    """Records undo calls; `undo_errors` maps an item id to the errors its undo raises in turn."""

    name = "fake"
    actions: ClassVar[dict[str, ActionSpec]] = {
        "make": ActionSpec(
            name="make", description="d", params_model=Params, reversible=True
        ),
    }

    def __init__(self, undo_errors: dict[str, list[Exception]] | None = None) -> None:
        self.undone: list[str] = []
        self.undo_errors = undo_errors or {}

    async def _run(self, action: str, params: BaseModel) -> ToolResult:
        raise NotImplementedError

    async def _undo(self, handle: UndoHandle) -> None:
        item = handle.data["id"]
        errors = self.undo_errors.get(item, [])
        if errors:
            raise errors.pop(0)
        self.undone.append(item)


def done(item: str, *, reversible: bool = True) -> CompletedAction:
    return CompletedAction(
        step_id=f"s-{item}",
        tool="fake",
        action="make",
        summary=f"made {item}",
        undo_handle=(
            UndoHandle(tool="fake", action="make", data={"id": item})
            if reversible
            else None
        ),
    )


class Rig:
    def __init__(self, undo_errors: dict[str, list[Exception]] | None = None) -> None:
        self.adapter = FakeAdapter(undo_errors)
        self.bus = EventBus()
        self.sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        self.ctx = StepContext(
            registry=ToolRegistry([self.adapter]),
            bus=self.bus,
            run_id="r",
            sleep=fake_sleep,
            backoff=float,
        )

    def types(self) -> list[str]:
        return [e.type for e in self.bus.history("r")]


async def test_actions_are_undone_newest_first() -> None:
    rig = Rig()
    result = await run_rollback([done("a"), done("b"), done("c")], rig.ctx)
    assert rig.adapter.undone == ["c", "b", "a"]
    assert [r.step_id for r in result.records] == ["s-c", "s-b", "s-a"]
    assert all(r.status == "undone" for r in result.records)
    assert result.all_undone


async def test_an_irreversible_action_is_skipped_not_failed() -> None:
    rig = Rig()
    result = await run_rollback([done("a"), done("n", reversible=False)], rig.ctx)
    assert [r.status for r in result.records] == ["skipped", "undone"]
    assert rig.adapter.undone == ["a"]
    assert result.all_undone  # a skipped notification leaves nothing to clean up


async def test_one_failed_undo_does_not_stop_the_others() -> None:
    rig = Rig({"b": [FatalToolError("page already deleted")]})
    result = await run_rollback([done("a"), done("b"), done("c")], rig.ctx)
    assert rig.adapter.undone == ["c", "a"]
    assert [r.status for r in result.records] == ["undone", "failed", "undone"]
    assert result.records[1].detail == "page already deleted"
    assert not result.all_undone


async def test_a_transient_undo_error_is_retried_then_succeeds() -> None:
    rig = Rig({"a": [TransientToolError("busy"), TransientToolError("busy")]})
    result = await run_rollback([done("a")], rig.ctx)
    assert result.records[0].status == "undone"
    assert rig.sleeps == [1.0, 2.0]


async def test_undo_retries_are_capped_at_three_then_reported_failed() -> None:
    rig = Rig({"a": [TransientToolError("busy") for _ in range(10)]})
    result = await run_rollback([done("a")], rig.ctx)
    assert result.records[0].status == "failed"
    assert rig.sleeps == [1.0, 2.0, 3.0]
    assert rig.adapter.undone == []


async def test_an_unexpected_exception_is_caught_without_leaking_its_message() -> None:
    rig = Rig({"b": [RuntimeError("secret user text")], "a": []})
    result = await run_rollback([done("a"), done("b")], rig.ctx)
    assert [r.status for r in result.records] == ["failed", "undone"]
    assert "RuntimeError" in result.records[0].detail
    assert "secret user text" not in str(result) + str(rig.bus.history("r"))


async def test_nothing_to_undo_gives_an_empty_result() -> None:
    rig = Rig()
    result = await run_rollback([], rig.ctx)
    assert result.records == [] and result.all_undone
    assert rig.types() == ["rollback_started"]


async def test_events_follow_the_work() -> None:
    rig = Rig()
    await run_rollback([done("a"), done("n", reversible=False), done("b")], rig.ctx)
    assert rig.types() == [
        "rollback_started",
        "rollback_step",
        "rollback_step",
        "rollback_step",
    ]
    events = rig.bus.history("r")
    assert events[0].data == {"total": 3, "to_undo": 2}
    assert [e.data["status"] for e in events[1:]] == ["undone", "skipped", "undone"]


def test_the_result_round_trips_through_json() -> None:
    result = RollbackResult(
        records=[
            RollbackRecord(
                step_id="s", tool="fake", action="make", status="skipped", detail="x"
            )
        ]
    )
    data: Any = result.model_dump_json()
    assert RollbackResult.model_validate_json(data) == result
