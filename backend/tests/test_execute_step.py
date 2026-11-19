from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from app.agent.failure_injection import Injection
from app.agent.nodes.execute_step import (
    StepContext,
    merge_hidden_params,
    notification_extras,
    run_step,
)
from app.agent.state import CompletedAction, PlanStep
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
    title: str = "t"
    folder_id: str | None = None


class FakeAdapter(ToolAdapter):
    """Plays back a scripted list of outcomes (an Exception is raised, anything else succeeds)."""

    name = "fake"
    actions: ClassVar[dict[str, ActionSpec]] = {
        "make": ActionSpec(
            name="make",
            description="d",
            params_model=Params,
            reversible=True,
            planner_hidden_params=frozenset({"folder_id"}),
        ),
        "make_safe": ActionSpec(
            name="make_safe",
            description="d",
            params_model=Params,
            reversible=True,
            repeat_safe=True,
        ),
        "notify": ActionSpec(
            name="notify", description="d", params_model=Params, reversible=False
        ),
    }

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def _run(self, action: str, params: BaseModel) -> ToolResult:
        assert isinstance(params, Params)
        self.calls.append(params.model_dump())
        outcome = self.script.pop(0) if self.script else None
        if isinstance(outcome, Exception):
            raise outcome
        reversible = self.actions[action].reversible
        return ToolResult(
            summary=f"{action} done",
            url="https://example.test/x",
            undo_handle=(
                UndoHandle(tool="fake", action=action, data={"id": "1"})
                if reversible
                else None
            ),
        )


class Rig:
    def __init__(self, *script: Any, injection: Injection | None = None, **inputs: Any):
        self.adapter = FakeAdapter(*script)
        self.bus = EventBus()
        self.sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        self.ctx = StepContext(
            registry=ToolRegistry([self.adapter]),
            bus=self.bus,
            run_id="r",
            inputs=inputs,
            injection=injection,
            sleep=fake_sleep,
            backoff=lambda n: float(n),  # 1, 2, 3 seconds: easy to assert
        )

    def types(self) -> list[str]:
        return [e.type for e in self.bus.history("r")]


def step(action: str = "make", **params: Any) -> PlanStep:
    return PlanStep(id="s1", tool="fake", action=action, params=params)


# ------------------------------------------------------------------ success


async def test_success_records_the_undo_handle_and_emits_events() -> None:
    rig = Rig()
    outcome = await run_step(step(), 1, rig.ctx)
    assert outcome.ok and outcome.retries_used == 0
    assert outcome.completed is not None
    assert outcome.completed.undo_handle == UndoHandle(
        tool="fake", action="make", data={"id": "1"}
    )
    assert outcome.record.status == "succeeded" and outcome.record.attempts == 1
    assert rig.types() == ["step_started", "step_succeeded"]


async def test_an_irreversible_action_records_no_undo_handle() -> None:
    outcome = await run_step(step("notify"), 1, Rig().ctx)
    assert outcome.ok and outcome.completed is not None
    assert outcome.completed.undo_handle is None


# ------------------------------------------------------------------ hidden params


def test_ui_inputs_fill_only_hidden_parameters() -> None:
    spec = FakeAdapter.actions["make"]
    merged = merge_hidden_params(
        spec,
        {"title": "from plan"},
        {"folder_id": "F1", "title": "ui must not win", "junk": "x"},
    )
    assert merged == {"title": "from plan", "folder_id": "F1"}


def test_missing_ui_input_leaves_the_default_to_the_adapter() -> None:
    spec = FakeAdapter.actions["make"]
    assert merge_hidden_params(spec, {"title": "t"}, {}) == {"title": "t"}
    assert merge_hidden_params(spec, {}, {"folder_id": ""}) == {}


async def test_the_executor_passes_the_merged_hidden_param_to_the_tool() -> None:
    rig = Rig(folder_id="F1")
    await run_step(step(title="Hello"), 1, rig.ctx)
    assert rig.adapter.calls == [{"title": "Hello", "folder_id": "F1"}]


# ------------------------------------------------------------------ retry


async def test_a_transient_error_is_retried_with_backoff_then_succeeds() -> None:
    rig = Rig(
        TransientToolError("rate limited", outcome_unknown=False),
        TransientToolError("rate limited", outcome_unknown=False),
    )
    outcome = await run_step(step(), 1, rig.ctx)
    assert outcome.ok and outcome.retries_used == 2
    assert outcome.record.attempts == 3
    assert rig.sleeps == [1.0, 2.0]
    assert rig.types() == [
        "step_started",
        "step_retry",
        "step_retry",
        "step_succeeded",
    ]
    retry_events = [e for e in rig.bus.history("r") if e.type == "step_retry"]
    assert [e.data["attempt"] for e in retry_events] == [2, 3]


async def test_retries_run_out_after_three_retries() -> None:
    errors = [TransientToolError("busy", outcome_unknown=False) for _ in range(4)]
    rig = Rig(*errors)
    outcome = await run_step(step(), 1, rig.ctx)
    assert not outcome.ok and outcome.completed is None
    assert outcome.record.attempts == 4 and outcome.retries_used == 3
    assert "gave up after 4 attempts" in (outcome.record.error or "")
    assert rig.sleeps == [1.0, 2.0, 3.0]
    assert rig.types()[-1] == "step_failed"


async def test_unknown_outcome_on_a_non_repeat_safe_action_is_not_retried() -> None:
    """A timed-out Notion page create may exist already: retrying could duplicate it."""
    rig = Rig(TransientToolError("timed out"))  # outcome_unknown defaults to True
    outcome = await run_step(step("make"), 1, rig.ctx)
    assert not outcome.ok
    assert "not retried" in (outcome.record.error or "")
    assert rig.sleeps == [] and len(rig.adapter.calls) == 1


async def test_unknown_outcome_on_a_repeat_safe_action_is_retried() -> None:
    rig = Rig(TransientToolError("timed out"))
    outcome = await run_step(step("make_safe"), 1, rig.ctx)
    assert outcome.ok and outcome.retries_used == 1


# ------------------------------------------------------------------ failures


async def test_a_fatal_error_fails_immediately_without_retry() -> None:
    rig = Rig(FatalToolError("no permission"))
    outcome = await run_step(step(), 1, rig.ctx)
    assert not outcome.ok and outcome.record.error == "no permission"
    assert rig.sleeps == [] and rig.types() == ["step_started", "step_failed"]


async def test_an_unknown_tool_is_a_failed_step_not_a_crash() -> None:
    rig = Rig()
    bad = PlanStep(id="s1", tool="nope", action="x")
    outcome = await run_step(bad, 1, rig.ctx)
    assert not outcome.ok and "Unknown tool" in (outcome.record.error or "")


async def test_an_unexpected_exception_becomes_a_failed_step_without_its_message() -> (
    None
):
    rig = Rig(RuntimeError("secret user text"))
    outcome = await run_step(step(), 1, rig.ctx)
    assert not outcome.ok
    assert "RuntimeError" in (outcome.record.error or "")
    assert "secret user text" not in (outcome.record.error or "")
    assert "secret user text" not in str(rig.bus.history("r"))


# ------------------------------------------------------------------ failure injection


async def test_injected_fatal_fails_before_any_real_call() -> None:
    rig = Rig(injection=Injection("s1", "fatal"))
    outcome = await run_step(step(), 1, rig.ctx)
    assert not outcome.ok and rig.adapter.calls == []


async def test_injected_transient_demonstrates_a_retry_then_the_real_call() -> None:
    rig = Rig(injection=Injection("1", "transient"))
    outcome = await run_step(step(), 1, rig.ctx)
    assert outcome.ok and outcome.retries_used == 2
    assert len(rig.adapter.calls) == 1  # only the successful attempt reached the tool


async def test_injection_for_another_step_changes_nothing() -> None:
    rig = Rig(injection=Injection("s9", "fatal"))
    assert (await run_step(step(), 1, rig.ctx)).ok


@pytest.mark.parametrize("position", [1, 2])
async def test_injection_by_position_targets_the_right_step(position: int) -> None:
    rig = Rig(injection=Injection("2", "fatal"))
    outcome = await run_step(step(), position, rig.ctx)
    assert outcome.ok is (position != 2)


# ------------------------------------------------------------------ notification extras


def _done(step_id: str, summary: str, url: str | None) -> CompletedAction:
    return CompletedAction(
        step_id=step_id, tool="fake", action="make", summary=summary, url=url
    )


def test_the_notification_gets_links_to_what_was_really_created() -> None:
    extras = notification_extras(
        [
            _done("s1", "Created Notion page", "https://notion.example/p1"),
            _done("s2", "Created Drive file", "http://drive.example/f1"),
        ]
    )
    assert extras == {
        "status": "success",
        "links": [
            {"label": "Created Notion page", "url": "https://notion.example/p1"},
            {"label": "Created Drive file", "url": "http://drive.example/f1"},
        ],
    }


def test_items_without_a_usable_url_are_left_out_of_the_links() -> None:
    extras = notification_extras(
        [
            _done("s1", "no url", None),
            _done("s2", "ftp", "ftp://example.test/x"),
            _done("s3", "script", "javascript:alert(1)"),
        ]
    )
    assert extras == {"status": "success"}


def test_links_are_capped_and_labels_clipped_to_the_notifiers_limits() -> None:
    many = [_done(f"s{i}", "L" * 300, f"https://e.test/{i}") for i in range(15)]
    links = notification_extras(many)["links"]
    assert len(links) == 10 and all(len(link["label"]) == 100 for link in links)


async def test_extra_params_reach_the_tool_but_not_the_recorded_step() -> None:
    rig = Rig()
    outcome = await run_step(step("make"), 1, rig.ctx, {"title": "from the executor"})
    assert rig.adapter.calls == [{"title": "from the executor", "folder_id": None}]
    assert outcome.record.step.params == {}  # the plan's own step is untouched
