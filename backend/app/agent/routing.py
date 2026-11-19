"""The decisions that steer a run, as pure functions so they can be tested without a graph.

Policy in one place:
  - A failed step normally means rollback.
  - A failed FINAL NOTIFICATION does not: the Notion/Drive work is fine, so the run ends as
    "completed_with_warning" instead of undoing good work because a chat message failed.
  - After rollback, "rolled_back" is only claimed when something was really undone and nothing
    was left behind; otherwise the honest label is "failed".
"""

from typing import Literal

from app.agent.state import (
    CompletedAction,
    RollbackRecord,
    RunStatus,
    WorkflowState,
)

# Names of the graph's nodes (they must differ from the state's keys, e.g. "plan", "report").
PARSE_INTENT = "parse_intent"
PLAN = "plan_steps"
EXECUTE = "execute_step"
ROLLBACK = "roll_back"
REPORT = "write_report"


def decide_after_step(
    *, ok: bool, is_last: bool, irreversible: bool, did_work: bool
) -> RunStatus:
    """The run status after one step. "running" means: go on to the next step."""
    if ok:
        return "completed" if is_last else "running"
    if irreversible and did_work:
        return "completed_with_warning"
    return "rolling_back"


def final_status_after_rollback(
    completed: list[CompletedAction], records: list[RollbackRecord]
) -> RunStatus:
    if not completed or any(r.status == "failed" for r in records):
        return "failed"
    return "rolled_back" if any(r.status == "undone" for r in records) else "failed"


def route_after_intent(state: WorkflowState) -> Literal["plan_steps", "write_report"]:
    return REPORT if state["status"] == "failed" else PLAN  # type: ignore[return-value]


def route_after_plan(state: WorkflowState) -> Literal["execute_step", "write_report"]:
    return REPORT if state["status"] == "failed" else EXECUTE  # type: ignore[return-value]


def route_after_step(
    state: WorkflowState,
) -> Literal["execute_step", "roll_back", "write_report"]:
    status = state["status"]
    if status == "running":
        return EXECUTE  # type: ignore[return-value]
    if status == "rolling_back":
        return ROLLBACK  # type: ignore[return-value]
    return REPORT  # type: ignore[return-value]
