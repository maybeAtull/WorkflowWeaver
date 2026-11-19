from typing import Any, ClassVar, cast

import pytest
from pydantic import BaseModel

from app.agent.deps import AgentDeps
from app.agent.nodes.verify import make_verify_node
from app.agent.state import CompletedAction, PlanStep, StepRecord, new_state
from app.events import EventBus
from app.tools.base import (
    ActionSpec,
    FatalToolError,
    ToolAdapter,
    ToolResult,
    TransientToolError,
    UndoHandle,
    VerifyResult,
)
from app.tools.registry import ToolRegistry


class Params(BaseModel):
    pass


class FakeAdapter(ToolAdapter):
    name = "fake"
    actions: ClassVar[dict[str, ActionSpec]] = {
        "make": ActionSpec(
            name="make", description="d", params_model=Params, reversible=True
        )
    }

    def __init__(self, script: dict[str, list[VerifyResult | Exception]]) -> None:
        self.script = script
        self.checked: list[str] = []

    async def _run(self, action: str, params: BaseModel) -> ToolResult:
        raise NotImplementedError

    async def _verify(self, handle: UndoHandle) -> VerifyResult:
        item = handle.data["id"]
        self.checked.append(item)
        outcomes = self.script.get(item, [])
        outcome = outcomes.pop(0) if outcomes else VerifyResult(status="verified")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class Unverifiable(ToolAdapter):
    """Keeps the default verify, like an adapter that has not implemented one."""

    name = "plain"
    actions: ClassVar[dict[str, ActionSpec]] = {
        "make": ActionSpec(
            name="make", description="d", params_model=Params, reversible=True
        )
    }

    async def _run(self, action: str, params: BaseModel) -> ToolResult:
        raise NotImplementedError


def done(item: str, *, reversible: bool = True, tool: str = "fake") -> CompletedAction:
    return CompletedAction(
        step_id=f"s-{item}",
        tool=tool,
        action="make",
        summary=f"made {item}",
        undo_handle=(
            UndoHandle(tool=tool, action="make", data={"id": item})
            if reversible
            else None
        ),
    )


class Rig:
    def __init__(
        self,
        completed: list[CompletedAction],
        script: dict[str, list[VerifyResult | Exception]] | None = None,
        *,
        remaining: int = 1,
    ) -> None:
        self.adapter = FakeAdapter(script or {})
        self.bus = EventBus()
        self.sleeps: list[float] = []

        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        async def no_planner(state: Any) -> Any:
            raise NotImplementedError

        self.deps = AgentDeps(
            registry=ToolRegistry([self.adapter, Unverifiable()]),
            bus=self.bus,
            planner=no_planner,
            intent_parser=no_planner,
            sleep=sleep,
            backoff=float,
        )
        state = new_state("r", "request")
        # `remaining` steps (the notification) are still to come after the checked ones.
        plan = [
            PlanStep(id=c.step_id, tool=c.tool, action="make") for c in completed
        ] + [
            PlanStep(id=f"later{i}", tool="fake", action="make")
            for i in range(remaining)
        ]
        state["plan"] = plan
        state["step_records"] = [
            StepRecord(step=s, status="succeeded" if i < len(completed) else "pending")
            for i, s in enumerate(plan)
        ]
        state["current_idx"] = len(completed)
        state["completed"] = completed
        self.state = state

    async def run(self) -> dict[str, Any]:
        return cast(dict[str, Any], await make_verify_node(self.deps)(self.state))


async def test_all_verified_carries_on_to_the_remaining_step() -> None:
    rig = Rig([done("a"), done("b")])
    update = await rig.run()
    assert update["status"] == "running" and update["verified"] is True
    assert [c.status for c in update["checks"]] == ["verified", "verified"]
    assert "error" not in update


async def test_with_nothing_left_to_run_the_run_is_complete() -> None:
    update = await Rig([done("a")], remaining=0).run()
    assert update["status"] == "completed"


async def test_only_actions_with_an_undo_handle_are_checked() -> None:
    rig = Rig([done("a"), done("n", reversible=False)])
    update = await rig.run()
    assert rig.adapter.checked == ["a"] and len(update["checks"]) == 1


async def test_a_failed_check_rolls_back_and_names_what_was_wrong() -> None:
    rig = Rig(
        [done("a"), done("b")],
        {"b": [VerifyResult(status="failed", detail="The page is in the trash.")]},
    )
    update = await rig.run()
    assert update["status"] == "rolling_back"
    assert update["error"] == "made b: The page is in the trash."
    # Every item is still checked, and the notification step never runs.
    assert rig.adapter.checked == ["a", "b"]
    assert [r.status for r in update["step_records"]][-1] == "skipped"


async def test_several_failures_are_all_reported_but_capped() -> None:
    items = [done(str(i)) for i in range(5)]
    script: dict[str, list[VerifyResult | Exception]] = {
        str(i): [VerifyResult(status="failed", detail="missing")] for i in range(5)
    }
    update = await Rig(items, script).run()
    assert update["error"].count("missing") == 3


async def test_unconfirmed_does_not_block() -> None:
    rig = Rig(
        [done("a")], {"a": [VerifyResult(status="unconfirmed", detail="cut off")]}
    )
    update = await rig.run()
    assert update["status"] == "running"
    assert update["checks"][0].status == "unconfirmed"


async def test_a_transient_read_is_retried_then_verified() -> None:
    rig = Rig(
        [done("a")], {"a": [TransientToolError("busy"), TransientToolError("busy")]}
    )
    update = await rig.run()
    assert update["checks"][0].status == "verified"
    assert rig.sleeps == [1.0, 2.0]


async def test_retries_run_out_to_unconfirmed() -> None:
    rig = Rig([done("a")], {"a": [TransientToolError("busy")] * 10})
    update = await rig.run()
    assert update["checks"][0].status == "unconfirmed"
    assert rig.sleeps == [1.0, 2.0, 3.0] and update["status"] == "running"


async def test_a_definite_service_error_counts_as_failed() -> None:
    rig = Rig([done("a")], {"a": [FatalToolError("object not found")]})
    update = await rig.run()
    assert update["checks"][0].status == "failed"
    assert update["status"] == "rolling_back"


async def test_an_unexpected_exception_fails_the_check_without_leaking_its_message() -> (
    None
):
    rig = Rig([done("a")], {"a": [RuntimeError("secret user text")]})
    update = await rig.run()
    assert update["checks"][0].status == "failed"
    assert "RuntimeError" in update["checks"][0].detail
    assert "secret user text" not in str(update) + str(rig.bus.history("r"))


async def test_an_adapter_without_a_check_is_honestly_unconfirmed() -> None:
    rig = Rig([done("x", tool="plain")])
    update = await rig.run()
    assert update["checks"][0].status == "unconfirmed"
    assert "cannot be checked" in update["checks"][0].detail
    assert update["status"] == "running"


async def test_events_announce_the_start_and_each_result() -> None:
    rig = Rig([done("a"), done("b")])
    await rig.run()
    events = rig.bus.history("r")
    assert [e.type for e in events] == ["verify_started", "verify_step", "verify_step"]
    assert events[0].data == {"total": 2}
    assert events[1].data["step_id"] == "s-a" and events[1].data["status"] == "verified"


async def test_the_registry_hands_a_handle_to_the_tool_that_made_it() -> None:
    adapter = FakeAdapter({})
    registry = ToolRegistry([adapter])
    handle = UndoHandle(tool="fake", action="make", data={"id": "z"})
    assert (await registry.verify(handle)).status == "verified"
    assert adapter.checked == ["z"]
    with pytest.raises(FatalToolError, match="Unknown tool"):
        await registry.verify(UndoHandle(tool="nope", action="make"))


async def test_an_adapter_refuses_a_handle_from_another_tool() -> None:
    with pytest.raises(FatalToolError, match="belongs to"):
        await FakeAdapter({}).verify(UndoHandle(tool="other", action="make"))
