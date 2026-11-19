"""The whole graph, run with fake tools and fixed plans: the decision flow without an LLM."""

from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from app.agent.deps import AgentDeps
from app.agent.failure_injection import Injection
from app.agent.graph import RECURSION_LIMIT, build_graph, run_workflow
from app.agent.nodes.plan import MAX_PLAN_STEPS, PlanningError
from app.agent.state import Intent, PlanStep, WorkflowState
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

INTENT = Intent(
    title="Weekly sync",
    summary="A short fictional sync.",
    kind="meeting_notes",
    action_items=[],
    decisions=[],
    requested_tools=[],
    clarification_needed="",
)


class Params(BaseModel):
    title: str = "t"
    folder_id: str | None = None


class FakeAdapter(ToolAdapter):
    """`make` is reversible; `notify` is not (like the Slack/Teams post).

    `fail` maps a title to the errors its create raises in turn; `undo_fail` does the same
    for its undo. Everything else succeeds.
    """

    name = "fake"
    actions: ClassVar[dict[str, ActionSpec]] = {
        "make": ActionSpec(
            name="make",
            description="d",
            params_model=Params,
            reversible=True,
            planner_hidden_params=frozenset({"folder_id"}),
        ),
        "notify": ActionSpec(
            name="notify", description="d", params_model=Params, reversible=False
        ),
    }

    def __init__(
        self,
        fail: dict[str, list[Exception]] | None = None,
        undo_fail: dict[str, list[Exception]] | None = None,
    ) -> None:
        self.fail = fail or {}
        self.undo_fail = undo_fail or {}
        self.calls: list[str] = []
        self.undone: list[str] = []

    async def _run(self, action: str, params: BaseModel) -> ToolResult:
        assert isinstance(params, Params)
        self.calls.append(params.title)
        errors = self.fail.get(params.title, [])
        if errors:
            raise errors.pop(0)
        handle = (
            UndoHandle(tool="fake", action="make", data={"id": params.title})
            if action == "make"
            else None
        )
        return ToolResult(
            summary=f"{action} {params.title}",
            url=f"https://example.test/{params.title}" if action == "make" else None,
            undo_handle=handle,
        )

    async def _undo(self, handle: UndoHandle) -> None:
        item = handle.data["id"]
        errors = self.undo_fail.get(item, [])
        if errors:
            raise errors.pop(0)
        self.undone.append(item)


def make(title: str) -> PlanStep:
    return PlanStep(
        id=f"s-{title}", tool="fake", action="make", params={"title": title}
    )


def notify(title: str = "ping") -> PlanStep:
    return PlanStep(
        id=f"s-{title}", tool="fake", action="notify", params={"title": title}
    )


class Rig:
    def __init__(
        self,
        plan: list[PlanStep] | Exception,
        fail: dict[str, list[Exception]] | None = None,
        undo_fail: dict[str, list[Exception]] | None = None,
        injection: Injection | None = None,
        intent: Intent | Exception = INTENT,
    ) -> None:
        self.adapter = FakeAdapter(fail, undo_fail)
        self.bus = EventBus()
        self.intent_calls = 0

        async def intent_parser(state: WorkflowState) -> Intent:
            self.intent_calls += 1
            if isinstance(intent, Exception):
                raise intent
            return intent

        self.sleeps: list[float] = []

        async def planner(state: WorkflowState) -> list[PlanStep]:
            if isinstance(plan, Exception):
                raise plan
            return plan

        async def fake_sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        self.deps = AgentDeps(
            registry=ToolRegistry([self.adapter]),
            bus=self.bus,
            planner=planner,
            intent_parser=intent_parser,
            injection=injection,
            sleep=fake_sleep,
            backoff=float,
        )

    async def run(self, **inputs: Any) -> WorkflowState:
        return await run_workflow(self.deps, "r", "do the thing", inputs)

    def types(self) -> list[str]:
        return [e.type for e in self.bus.history("r")]


def statuses(state: WorkflowState) -> list[str]:
    return [r.status for r in state["step_records"]]


# ------------------------------------------------------------------ happy path


async def test_a_successful_run_completes_in_order_with_undo_handles_stored() -> None:
    rig = Rig([make("a"), make("b"), notify()])
    final = await rig.run()
    assert final["status"] == "completed" and final["error"] is None
    assert rig.adapter.calls == ["a", "b", "ping"]
    assert [c.step_id for c in final["completed"]] == ["s-a", "s-b", "s-ping"]
    assert [bool(c.undo_handle) for c in final["completed"]] == [True, True, False]
    assert statuses(final) == ["succeeded"] * 3
    assert rig.adapter.undone == []  # nothing is undone on success


async def test_events_run_in_order_and_the_stream_ends_with_run_completed() -> None:
    rig = Rig([make("a"), notify()])
    await rig.run()
    assert rig.types() == [
        "run_started",
        "plan_ready",
        "step_started",
        "step_succeeded",
        "step_started",
        "step_succeeded",
        "run_completed",
    ]
    assert rig.bus.history("r")[-1].data["status"] == "completed"


async def test_the_report_summarises_every_step() -> None:
    final = await Rig([make("a"), notify()]).run()
    report = final["report"]
    assert report is not None and report["status"] == "completed"
    assert [s["id"] for s in report["steps"]] == ["s-a", "s-ping"]
    assert report["rollback"] == [] and report["retries"] == 0


async def test_the_notification_receives_links_to_the_items_that_were_created() -> None:
    rig = Rig([make("a"), make("b"), notify()])
    sent: list[dict[str, Any]] = []
    original = rig.deps.registry.execute

    async def spy(tool: str, action: str, params: Any) -> ToolResult:
        if action == "notify":
            sent.append(dict(params))
        return await original(tool, action, params)

    rig.deps.registry.execute = spy  # type: ignore[method-assign]
    await rig.run()
    assert sent[0]["status"] == "success"
    assert sent[0]["links"] == [
        {"label": "make a", "url": "https://example.test/a"},
        {"label": "make b", "url": "https://example.test/b"},
    ]


async def test_ui_inputs_reach_the_tool_as_hidden_parameters() -> None:
    rig = Rig([make("a")])
    seen: list[dict[str, Any]] = []
    original = rig.adapter._run

    async def spy(action: str, params: BaseModel) -> ToolResult:
        seen.append(params.model_dump())
        return await original(action, params)

    rig.adapter._run = spy  # type: ignore[method-assign]
    await rig.run(folder_id="F1")
    assert seen == [{"title": "a", "folder_id": "F1"}]


# ------------------------------------------------------------------ rollback


async def test_a_fatal_failure_rolls_back_earlier_steps_in_reverse() -> None:
    rig = Rig(
        [make("a"), make("b"), make("c"), notify()],
        fail={"c": [FatalToolError("boom")]},
    )
    final = await rig.run()
    assert final["status"] == "rolled_back" and final["error"] == "boom"
    assert rig.adapter.undone == ["b", "a"]
    assert [r.status for r in final["rollbacks"]] == ["undone", "undone"]
    assert statuses(final) == ["succeeded", "succeeded", "failed", "skipped"]
    assert "ping" not in rig.adapter.calls  # the notification never went out
    assert rig.types()[-1] == "run_completed"
    assert rig.bus.history("r")[-1].data["status"] == "rolled_back"


async def test_a_failure_at_the_first_step_has_nothing_to_undo() -> None:
    rig = Rig([make("a"), notify()], fail={"a": [FatalToolError("no access")]})
    final = await rig.run()
    assert final["status"] == "failed"
    assert final["rollbacks"] == [] and rig.adapter.undone == []


async def test_a_failed_undo_makes_the_run_failed_and_is_reported() -> None:
    rig = Rig(
        [make("a"), make("b"), make("c")],
        fail={"c": [FatalToolError("boom")]},
        undo_fail={"b": [FatalToolError("cannot delete")]},
    )
    final = await rig.run()
    assert final["status"] == "failed"
    assert rig.adapter.undone == ["a"]  # b failed, a still got cleaned up
    failed = [r for r in final["rollbacks"] if r.status == "failed"]
    assert [r.step_id for r in failed] == ["s-b"]
    assert final["report"] is not None and len(final["report"]["rollback"]) == 2


# ------------------------------------------------------------------ the notification


async def test_a_failed_final_notification_keeps_the_work_with_a_warning() -> None:
    rig = Rig(
        [make("a"), make("b"), notify()],
        fail={"ping": [FatalToolError("webhook gone")]},
    )
    final = await rig.run()
    assert final["status"] == "completed_with_warning"
    assert rig.adapter.undone == []  # good work is not undone for a failed message
    assert final["rollbacks"] == []
    assert statuses(final) == ["succeeded", "succeeded", "failed"]
    assert final["error"] == "webhook gone"


# ------------------------------------------------------------------ retry and injection


async def test_a_transient_error_is_retried_and_counted_in_state() -> None:
    err = TransientToolError("busy", outcome_unknown=False)
    rig = Rig([make("a"), notify()], fail={"a": [err, err]})
    final = await rig.run()
    assert final["status"] == "completed"
    assert final["retries"] == 2 and rig.sleeps == [1.0, 2.0]
    assert final["step_records"][0].attempts == 3


async def test_injected_fatal_failure_triggers_a_real_rollback() -> None:
    rig = Rig([make("a"), make("b"), notify()], injection=Injection("s-b", "fatal"))
    final = await rig.run()
    assert final["status"] == "rolled_back"
    assert rig.adapter.calls == ["a"]  # the injected failure never reached the tool
    assert rig.adapter.undone == ["a"]


async def test_injected_transient_failure_shows_retries_then_completes() -> None:
    rig = Rig([make("a"), notify()], injection=Injection("1", "transient"))
    final = await rig.run()
    assert final["status"] == "completed" and final["retries"] == 2
    assert rig.types().count("step_retry") == 2


# ------------------------------------------------------------------ planning problems


@pytest.mark.parametrize(
    "plan",
    [
        [],
        PlanningError("I could not understand that."),
        RuntimeError("secret user text"),
    ],
)
async def test_a_bad_plan_ends_the_run_without_touching_any_tool(
    plan: list[PlanStep] | Exception,
) -> None:
    rig = Rig(plan)
    final = await rig.run()
    assert final["status"] == "failed" and final["error"]
    assert rig.adapter.calls == [] and final["step_records"] == []
    assert rig.types() == ["run_started", "run_completed"]
    assert "secret user text" not in (final["error"] or "")


async def test_a_planning_error_message_reaches_the_user() -> None:
    final = await Rig(PlanningError("I could not understand that.")).run()
    assert final["error"] == "I could not understand that."


async def test_a_plan_at_the_step_limit_runs_and_one_over_is_rejected() -> None:
    ok = await Rig([make(f"m{i}") for i in range(MAX_PLAN_STEPS)]).run()
    assert ok["status"] == "completed"
    too_long = await Rig([make(f"m{i}") for i in range(MAX_PLAN_STEPS + 1)]).run()
    assert too_long["status"] == "failed" and "at most" in (too_long["error"] or "")


async def test_a_full_length_plan_that_rolls_back_stays_inside_the_recursion_limit() -> (
    None
):
    steps = [make(f"m{i}") for i in range(MAX_PLAN_STEPS)]
    rig = Rig(steps, fail={f"m{MAX_PLAN_STEPS - 1}": [FatalToolError("late failure")]})
    final = await rig.run()
    assert final["status"] == "rolled_back"
    assert len(rig.adapter.undone) == MAX_PLAN_STEPS - 1
    assert RECURSION_LIMIT >= MAX_PLAN_STEPS + 3  # plan + steps + rollback + report


# ------------------------------------------------------------------ understanding the request


async def test_the_parsed_intent_is_stored_in_the_state() -> None:
    final = await Rig([make("a")]).run()
    assert final["intent"] == INTENT


async def test_the_planner_sees_the_intent() -> None:
    rig = Rig([make("a")])
    seen: list[Intent | None] = []
    inner = rig.deps.planner

    async def spying_planner(state: WorkflowState) -> list[PlanStep]:
        seen.append(state["intent"])
        return await inner(state)

    rig.deps.planner = spying_planner
    await rig.run()
    assert seen == [INTENT]


@pytest.mark.parametrize(
    "intent",
    [PlanningError("What should the page say?"), RuntimeError("secret user text")],
)
async def test_a_request_that_cannot_be_understood_stops_before_planning_or_any_tool(
    intent: Exception,
) -> None:
    rig = Rig([make("a")], intent=intent)
    final = await rig.run()
    assert final["status"] == "failed" and final["intent"] is None
    assert rig.adapter.calls == [] and final["plan"] == []
    assert rig.types() == ["run_started", "run_completed"]
    assert "secret user text" not in (final["error"] or "")


async def test_a_clarifying_question_reaches_the_user_as_the_error() -> None:
    final = await Rig(
        [make("a")], intent=PlanningError("What should the page say?")
    ).run()
    assert final["error"] == "What should the page say?"


# ------------------------------------------------------------------ structure


def test_the_graph_can_draw_itself_for_the_docs() -> None:
    diagram = build_graph(Rig([make("a")]).deps).get_graph().draw_mermaid()
    for node in (
        "parse_intent",
        "plan_steps",
        "execute_step",
        "roll_back",
        "write_report",
    ):
        assert node in diagram
