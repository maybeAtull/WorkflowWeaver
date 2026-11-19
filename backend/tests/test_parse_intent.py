from typing import Any, cast

import pytest

from app.agent.llm_client import LlmError
from app.agent.nodes.parse_intent import (
    MAX_REQUEST_CHARS,
    MAX_TITLE_CHARS,
    make_llm_intent_parser,
    make_parse_intent_node,
)
from app.agent.nodes.plan import PlanningError
from app.agent.prompts import INTENT_SYSTEM
from app.agent.state import ActionItem, Intent, WorkflowState, new_state


def intent(**changes: Any) -> Intent:
    base = {
        "title": "Weekly sync",
        "summary": "A short fictional sync.",
        "kind": "meeting_notes",
        "action_items": [ActionItem(task="Book the room", owner="Priya", due="")],
        "decisions": ["Launch stays on the 14th"],
        "requested_tools": ["notion", "notifier"],
        "clarification_needed": "",
    }
    return Intent(**{**base, **changes})


class FakeClient:
    def __init__(self, result: Intent | Exception) -> None:
        self.result = result
        self.calls: list[tuple[str, str, type]] = []

    async def ask(
        self, system: str, human: str, schema: type, name: str = ""
    ) -> Intent:
        self.calls.append((system, human, schema))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def state_for(request: str) -> WorkflowState:
    return new_state("r", request)


def parser(client: FakeClient) -> Any:
    return make_llm_intent_parser(client)  # type: ignore[arg-type]


async def test_the_intent_comes_back_with_the_prompt_and_schema_used() -> None:
    client = FakeClient(intent())
    result = await parser(client)(state_for("Notes: Priya books the room."))
    assert result == intent()
    system, human, schema = client.calls[0]
    assert system == INTENT_SYSTEM and schema is Intent
    assert "Notes: Priya books the room." in human


async def test_the_request_is_trimmed_before_it_is_sent() -> None:
    client = FakeClient(intent())
    await parser(client)(state_for("   hello   \n"))
    assert "\nhello\n" in client.calls[0][1]


@pytest.mark.parametrize("request_text", ["", "   \n\t "])
async def test_an_empty_request_is_rejected_before_any_llm_call(
    request_text: str,
) -> None:
    client = FakeClient(intent())
    with pytest.raises(PlanningError, match="describe the workflow"):
        await parser(client)(state_for(request_text))
    assert client.calls == []


async def test_a_request_over_the_limit_is_rejected_not_truncated() -> None:
    client = FakeClient(intent())
    with pytest.raises(PlanningError, match="limit is 6,000"):
        await parser(client)(state_for("x" * (MAX_REQUEST_CHARS + 1)))
    assert client.calls == []


async def test_a_request_exactly_at_the_limit_is_accepted() -> None:
    client = FakeClient(intent())
    await parser(client)(state_for("x" * MAX_REQUEST_CHARS))
    assert len(client.calls) == 1


async def test_a_clarifying_question_ends_the_run_with_that_question() -> None:
    client = FakeClient(intent(clarification_needed="  What should the page say? "))
    with pytest.raises(PlanningError) as info:
        await parser(client)(state_for("Make me a page"))
    assert str(info.value) == "What should the page say?"


async def test_an_llm_failure_becomes_a_planning_error_with_its_safe_message() -> None:
    client = FakeClient(LlmError("The AI service is busy.", "transient"))
    with pytest.raises(PlanningError, match="The AI service is busy."):
        await parser(client)(state_for("notes"))


async def test_a_long_title_is_trimmed() -> None:
    client = FakeClient(intent(title="T" * 200))
    result = await parser(client)(state_for("notes"))
    assert len(result.title) == MAX_TITLE_CHARS


# ------------------------------------------------------------------ the graph node


class Deps:
    def __init__(self, behaviour: Intent | Exception) -> None:
        self.behaviour = behaviour

    async def intent_parser(self, state: WorkflowState) -> Intent:
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return self.behaviour


def node(behaviour: Intent | Exception) -> Any:
    return make_parse_intent_node(cast(Any, Deps(behaviour)))


async def test_the_node_stores_the_intent() -> None:
    update = await node(intent())(state_for("notes"))
    assert update == {"intent": intent()}


async def test_the_node_turns_a_planning_error_into_a_failed_run() -> None:
    update = await node(PlanningError("What should the page say?"))(state_for("n"))
    assert update == {"status": "failed", "error": "What should the page say?"}


async def test_the_node_survives_a_crash_without_leaking_its_message() -> None:
    update = await node(RuntimeError("secret user text"))(state_for("n"))
    assert update["status"] == "failed"
    assert "RuntimeError" in update["error"] and "secret" not in update["error"]
