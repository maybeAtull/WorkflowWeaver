"""The agent's state: everything the graph knows about one run, in one place.

LangGraph needs the top-level state to be a TypedDict (its reducers work on keys); the values
inside are Pydantic models so they are validated and JSON-serializable. That matters later:
the state goes into SQLite, the SSE stream and the execution report.

Size rule: state holds summaries and ids, never large text (meeting notes, generated page
bodies). Those would bloat Langfuse traces and the LLM's token budget.
"""

import operator
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field

from app.tools.base import UndoHandle

# planning -> running -> one of the four final states. "rolling_back" is the detour taken after
# a fatal error. "completed_with_warning" = all work done but the closing notification failed:
# the Notion/Drive items are fine, so they are kept rather than rolled back.
RunStatus = Literal[
    "planning",
    "running",
    "rolling_back",
    "completed",
    "completed_with_warning",
    "failed",
    "rolled_back",
]
FINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "completed_with_warning", "failed", "rolled_back"}
)

StepStatus = Literal["pending", "running", "succeeded", "failed", "skipped"]


class PlanStep(BaseModel):
    """One planned action. `params` is what the planner (LLM) chose; hidden parameters such
    as folder ids are merged in by the executor, never by the LLM."""

    id: str
    tool: str
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    description: str = ""  # one line for the live timeline


class StepRecord(BaseModel):
    """Progress of one step, shown on the timeline and in the report."""

    step: PlanStep
    status: StepStatus = "pending"
    attempts: int = 0
    summary: str | None = None
    url: str | None = None
    error: str | None = None  # already redacted by the tool layer


class CompletedAction(BaseModel):
    """An executed step, kept so rollback can reverse it. Appended BEFORE the next step runs.

    `undo_handle` is None for irreversible actions (the notification).
    """

    step_id: str
    tool: str
    action: str
    summary: str
    url: str | None = None
    undo_handle: UndoHandle | None = None


class RollbackRecord(BaseModel):
    """Outcome of undoing one completed action."""

    step_id: str
    tool: str
    action: str
    status: Literal["undone", "failed"]
    detail: str = ""


class WorkflowState(TypedDict, total=False):
    run_id: str
    user_request: str
    inputs: dict[str, Any]  # values chosen in the UI (parent page, folder, ...)
    status: RunStatus
    plan: list[PlanStep]
    current_idx: int  # index of the step to run next
    step_records: list[StepRecord]
    # Appended to (operator.add), never overwritten: a node returns only the NEW entries.
    completed: Annotated[list[CompletedAction], operator.add]
    rollbacks: Annotated[list[RollbackRecord], operator.add]
    retries: int  # total retry attempts in this run (for the report and Langfuse score)
    error: str | None
    report: dict[str, Any] | None


def new_state(
    run_id: str, user_request: str, inputs: dict[str, Any] | None = None
) -> WorkflowState:
    """The starting state of a run."""
    return WorkflowState(
        run_id=run_id,
        user_request=user_request,
        inputs=dict(inputs or {}),
        status="planning",
        plan=[],
        current_idx=0,
        step_records=[],
        completed=[],
        rollbacks=[],
        retries=0,
        error=None,
        report=None,
    )
