from typing import Any

import pytest

from app.agent.llm_client import LlmError
from app.agent.nodes.plan import MAX_PLAN_STEPS, PlanningError
from app.agent.planner import (
    PlannedParam,
    PlannedStep,
    PlannedWorkflow,
    make_llm_planner,
    plan_problems,
    to_params,
)
from app.agent.state import ActionItem, Intent, PlanStep, WorkflowState, new_state
from app.config import Settings
from app.tools.registry import ToolRegistry, build_registry
from tests.test_config import FAKE_ENV


class NoMcp:
    async def call_tool(self, *args: Any, **kwargs: Any) -> str:
        raise AssertionError("planning must not call any tool")


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> ToolRegistry:
    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    return build_registry(Settings(_env_file=None), NoMcp())  # type: ignore[call-arg]


def p(name: str, value: str = "", items: list[str] | None = None) -> PlannedParam:
    return PlannedParam(name=name, value=value, items=items or [])


def page(title: str = "Weekly sync", items: list[str] | None = None) -> PlannedStep:
    return PlannedStep(
        tool="notion",
        action="create_page",
        description="Create the notes page.",
        params=[
            p("title", title),
            p("content", "Summary text"),
            p("action_items", "", items or ["Book the room (Priya)"]),
        ],
    )


def file() -> PlannedStep:
    return PlannedStep(
        tool="drive",
        action="create_text_file",
        description="Save a copy.",
        params=[p("name", "notes.md"), p("content", "Summary text")],
    )


def notify() -> PlannedStep:
    return PlannedStep(
        tool="notifier",
        action="send",
        description="Tell the team.",
        params=[p("title", "Notes ready"), p("summary", "Done.")],
    )


def intent(requested: list[str] | None = None) -> Intent:
    return Intent(
        title="Weekly sync",
        summary="A short fictional sync.",
        kind="meeting_notes",
        action_items=[ActionItem(task="Book the room", owner="Priya", due="")],
        decisions=["Launch stays on the 14th"],
        requested_tools=["notion", "drive", "notifier"] if requested is None else requested,  # type: ignore[arg-type]
        clarification_needed="",
    )


def state_with(intent_value: Intent | None) -> WorkflowState:
    state = new_state("r", "request")
    state["intent"] = intent_value
    return state


class FakeClient:
    """Returns scripted workflows (or raises scripted errors); remembers every prompt."""

    def __init__(self, *script: PlannedWorkflow | Exception) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, str]] = []

    async def ask(self, system: str, human: str, schema: type, name: str = "") -> Any:
        assert schema is PlannedWorkflow
        self.calls.append((system, human))
        outcome = self.script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def planner(client: FakeClient, registry: ToolRegistry) -> Any:
    return make_llm_planner(client, registry)  # type: ignore[arg-type]


GOOD = PlannedWorkflow(steps=[page(), file(), notify()])


# ------------------------------------------------------------------ the happy path


async def test_a_valid_plan_becomes_steps_with_ids_made_by_code(
    registry: ToolRegistry,
) -> None:
    steps = await planner(FakeClient(GOOD), registry)(state_with(intent()))
    assert [s.id for s in steps] == ["s1", "s2", "s3"]
    assert [(s.tool, s.action) for s in steps] == [
        ("notion", "create_page"),
        ("drive", "create_text_file"),
        ("notifier", "send"),
    ]
    assert steps[0].params == {
        "title": "Weekly sync",
        "content": "Summary text",
        "action_items": ["Book the room (Priya)"],
    }
    assert steps[0].description == "Create the notes page."


async def test_one_llm_call_when_the_first_plan_is_valid(
    registry: ToolRegistry,
) -> None:
    client = FakeClient(GOOD)
    await planner(client, registry)(state_with(intent()))
    assert len(client.calls) == 1


async def test_the_prompts_show_the_catalogue_and_hide_what_the_executor_supplies(
    registry: ToolRegistry,
) -> None:
    client = FakeClient(GOOD)
    await planner(client, registry)(state_with(intent()))
    system, human = client.calls[0]
    for visible in ("notion.create_page", "drive.create_text_file", "notifier.send"):
        assert visible in system
    for hidden in ("parent_page_id", "folder_id", "links", "facts", "status"):
        assert hidden not in system.split("Rules:")[0]
    assert "Priya" in human and "Requested tools: notion, drive, notifier" in human


# ------------------------------------------------------------------ pairs to params


def test_a_list_parameter_given_as_a_single_value_is_forgiven(
    registry: ToolRegistry,
) -> None:
    params = to_params(
        registry, "notion", "create_page", [p("action_items", "one task")]
    )
    assert params == {"action_items": ["one task"]}


def test_blank_list_entries_and_blank_text_are_dropped(registry: ToolRegistry) -> None:
    params = to_params(
        registry,
        "notion",
        "create_page",
        [p("title", "T"), p("content", ""), p("action_items", "", ["a", "  ", "b"])],
    )
    assert params == {"title": "T", "action_items": ["a", "b"]}


def test_an_unknown_tool_still_converts_so_validation_can_report_it(
    registry: ToolRegistry,
) -> None:
    assert to_params(registry, "slack", "post", [p("text", "hi")]) == {"text": "hi"}


# ------------------------------------------------------------------ plan checks


def steps_of(*planned: PlannedStep, registry: ToolRegistry) -> list[PlanStep]:
    return [
        PlanStep(
            id=f"s{n}",
            tool=s.tool,
            action=s.action,
            params=to_params(registry, s.tool, s.action, s.params),
        )
        for n, s in enumerate(planned, start=1)
    ]


REQUESTED = ["notion", "drive", "notifier"]


def test_a_correct_plan_has_no_problems(registry: ToolRegistry) -> None:
    steps = steps_of(page(), file(), notify(), registry=registry)
    assert plan_problems(registry, steps, REQUESTED) == []


def test_an_empty_plan_is_a_problem(registry: ToolRegistry) -> None:
    assert plan_problems(registry, [], REQUESTED) == ["the plan has no steps"]


def test_an_unknown_tool_is_reported(registry: ToolRegistry) -> None:
    bad = PlannedStep(tool="email", action="send", description="", params=[])
    steps = steps_of(page(), file(), bad, registry=registry)
    problems = plan_problems(registry, steps, REQUESTED)
    assert any("unknown tool 'email'" in x for x in problems)


def test_a_settings_supplied_id_chosen_by_the_model_is_rejected(
    registry: ToolRegistry,
) -> None:
    sneaky = page()
    sneaky.params.append(p("parent_page_id", "made-up"))
    problems = plan_problems(
        registry, steps_of(sneaky, file(), notify(), registry=registry), REQUESTED
    )
    assert any("parent_page_id" in x for x in problems)


def test_a_bad_value_is_reported_without_echoing_it(registry: ToolRegistry) -> None:
    bad_file = PlannedStep(
        tool="drive",
        action="create_text_file",
        description="",
        params=[p("name", "SECRET-USER-TEXT.docx")],
    )
    problems = plan_problems(
        registry, steps_of(page(), bad_file, notify(), registry=registry), REQUESTED
    )
    assert any("create_text_file.name" in x for x in problems)
    assert "SECRET-USER-TEXT" not in " ".join(problems)


def test_the_notification_must_be_last(registry: ToolRegistry) -> None:
    steps = steps_of(page(), notify(), file(), registry=registry)
    problems = plan_problems(registry, steps, REQUESTED)
    assert any("must be the last step" in x for x in problems)


def test_two_notifications_are_rejected(registry: ToolRegistry) -> None:
    steps = steps_of(page(), file(), notify(), notify(), registry=registry)
    assert any("last step" in x for x in plan_problems(registry, steps, REQUESTED))


def test_every_requested_tool_must_be_used_and_no_other(registry: ToolRegistry) -> None:
    steps = steps_of(page(), notify(), registry=registry)
    problems = plan_problems(registry, steps, REQUESTED)
    assert "the request asks for drive but no step uses it" in problems
    extra = plan_problems(registry, steps, ["notion"])
    assert "the plan uses notifier, which was not requested" in extra


def test_too_many_steps_is_a_problem(registry: ToolRegistry) -> None:
    steps = steps_of(*[page() for _ in range(MAX_PLAN_STEPS + 1)], registry=registry)
    problems = plan_problems(registry, steps, ["notion"])
    assert any("at most" in x for x in problems)


# ------------------------------------------------------------------ repair


async def test_a_rejected_plan_is_repaired_once_with_the_problems_in_the_prompt(
    registry: ToolRegistry,
) -> None:
    missing_drive = PlannedWorkflow(steps=[page(), notify()])
    client = FakeClient(missing_drive, GOOD)
    steps = await planner(client, registry)(state_with(intent()))
    assert [s.tool for s in steps] == ["notion", "drive", "notifier"]
    assert len(client.calls) == 2
    first_human, repair_human = client.calls[0][1], client.calls[1][1]
    assert first_human in repair_human
    assert "asks for drive but no step uses it" in repair_human


async def test_two_bad_plans_end_with_a_plain_message_and_the_detail_only_in_the_log(
    registry: ToolRegistry, caplog: pytest.LogCaptureFixture
) -> None:
    bad = PlannedWorkflow(steps=[page(), notify()])
    client = FakeClient(bad, bad)
    with pytest.raises(PlanningError) as info:
        await planner(client, registry)(state_with(intent()))
    assert len(client.calls) == 2  # exactly one repair, never a loop
    assert "reliable plan" in str(info.value)
    assert "drive" not in str(info.value)
    assert "asks for drive" in caplog.text


# ------------------------------------------------------------------ refusing early


async def test_no_requested_tool_means_a_question_not_a_guess(
    registry: ToolRegistry,
) -> None:
    client = FakeClient(GOOD)
    with pytest.raises(PlanningError, match="where the results should go"):
        await planner(client, registry)(state_with(intent(requested=[])))
    assert client.calls == []


async def test_planning_without_an_intent_is_refused(registry: ToolRegistry) -> None:
    with pytest.raises(PlanningError):
        await planner(FakeClient(GOOD), registry)(state_with(None))


async def test_an_llm_failure_becomes_a_planning_error_with_its_safe_message(
    registry: ToolRegistry,
) -> None:
    client = FakeClient(LlmError("The AI service is busy.", "transient"))
    with pytest.raises(PlanningError, match="The AI service is busy."):
        await planner(client, registry)(state_with(intent()))


async def test_an_llm_failure_during_repair_is_also_reported(
    registry: ToolRegistry,
) -> None:
    client = FakeClient(
        PlannedWorkflow(steps=[page(), notify()]),
        LlmError("The AI service has reached its usage limit.", "quota"),
    )
    with pytest.raises(PlanningError, match="usage limit"):
        await planner(client, registry)(state_with(intent()))
