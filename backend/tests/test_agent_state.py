from langgraph.graph import END, START, StateGraph

from app.agent.state import (
    FINAL_STATUSES,
    ActionItem,
    CompletedAction,
    Intent,
    PlanStep,
    RollbackRecord,
    StepRecord,
    WorkflowState,
    new_state,
)
from app.tools.base import UndoHandle


def test_new_state_starts_in_planning_with_empty_collections() -> None:
    state = new_state("run-1", "do a thing", {"folder": "x"})
    assert state["status"] == "planning"
    assert state["current_idx"] == 0
    assert state["plan"] == state["completed"] == state["rollbacks"] == []
    assert state["retries"] == 0 and state["error"] is None and state["report"] is None
    assert state["inputs"] == {"folder": "x"}


def test_new_state_does_not_share_the_callers_inputs_dict() -> None:
    inputs = {"a": "1"}
    state = new_state("r", "req", inputs)
    inputs["a"] = "changed"
    assert state["inputs"] == {"a": "1"}


def test_final_statuses_are_exactly_the_four_end_states() -> None:
    assert FINAL_STATUSES == {
        "completed",
        "completed_with_warning",
        "failed",
        "rolled_back",
    }


def test_models_round_trip_through_json() -> None:
    step = PlanStep(id="s1", tool="notion", action="create_page", params={"title": "T"})
    handle = UndoHandle(tool="notion", action="create_page", data={"page_id": "p"})
    models = [
        step,
        StepRecord(step=step, status="succeeded", attempts=2, summary="ok"),
        CompletedAction(
            step_id="s1",
            tool="notion",
            action="create_page",
            summary="ok",
            undo_handle=handle,
        ),
        RollbackRecord(
            step_id="s1", tool="notion", action="create_page", status="undone"
        ),
    ]
    for model in models:
        assert type(model).model_validate_json(model.model_dump_json()) == model


def test_a_new_run_has_no_intent_yet_and_an_intent_round_trips_through_json() -> None:
    assert new_state("r", "req")["intent"] is None
    intent = Intent(
        title="T",
        summary="S",
        kind="incident",
        action_items=[ActionItem(task="Fix it", owner="Sam", due="")],
        decisions=["Roll back the release"],
        requested_tools=["notion", "notifier"],
        clarification_needed="",
    )
    assert Intent.model_validate_json(intent.model_dump_json()) == intent


def test_the_intent_schema_has_only_required_fields_for_strict_providers() -> None:
    """No optionals or defaults: strict structured-output modes reject them."""
    schema = Intent.model_json_schema()
    assert set(schema["required"]) == set(schema["properties"])
    # A free-form dict field would show up as "additionalProperties" in the schema.
    assert "additionalProperties" not in str(schema)
    assert "default" not in str(schema)


def test_an_irreversible_action_has_no_undo_handle() -> None:
    notified = CompletedAction(
        step_id="s9", tool="notifier", action="send", summary="posted"
    )
    assert notified.undo_handle is None


def test_completed_is_appended_to_not_overwritten_by_the_graph() -> None:
    """The reducer is what lets a node return only its NEW completed action."""

    def first(state: WorkflowState) -> dict:
        return {
            "completed": [
                CompletedAction(step_id="s1", tool="notion", action="a", summary="1")
            ]
        }

    def second(state: WorkflowState) -> dict:
        return {
            "completed": [
                CompletedAction(step_id="s2", tool="drive", action="b", summary="2")
            ]
        }

    graph = StateGraph(WorkflowState)
    graph.add_node("first", first)
    graph.add_node("second", second)
    graph.add_edge(START, "first")
    graph.add_edge("first", "second")
    graph.add_edge("second", END)

    final = graph.compile().invoke(new_state("r", "req"))
    assert [c.step_id for c in final["completed"]] == ["s1", "s2"]
