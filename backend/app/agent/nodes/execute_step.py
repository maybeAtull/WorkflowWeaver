"""Run ONE planned step safely: merge hidden params, execute, retry when safe, record the undo.

This is plain logic with no LLM and no graph, so every branch is unit-testable. Task 2.4 wraps
it in a graph node; rollback (Task 2.3) consumes what it records. It never decides what comes
next and never rolls back: it reports the outcome faithfully.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.agent.deps import AgentDeps
from app.agent.failure_injection import Injection
from app.agent.retry import MAX_RETRIES, backoff_delay
from app.agent.routing import decide_after_step
from app.agent.state import CompletedAction, PlanStep, StepRecord, WorkflowState
from app.events import EventBus
from app.tools.base import ActionSpec, ToolError, TransientToolError, is_safe_to_retry
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class StepContext:
    """What a step needs from its run. Sleep and backoff are injectable so tests never wait."""

    registry: ToolRegistry
    bus: EventBus
    run_id: str
    inputs: Mapping[str, Any] = field(default_factory=dict)  # choices made in the UI
    injection: Injection | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    backoff: Callable[[int], float] = backoff_delay


class StepOutcome(BaseModel):
    ok: bool
    record: StepRecord
    # Set on success. The caller must store it in state before running the next step.
    completed: CompletedAction | None = None
    retries_used: int = 0


def context_for(deps: AgentDeps, state: WorkflowState) -> StepContext:
    """The per-run context a step or a rollback needs, built from the deps and the state."""
    return StepContext(
        registry=deps.registry,
        bus=deps.bus,
        run_id=state["run_id"],
        inputs=state["inputs"],
        injection=deps.injection,
        sleep=deps.sleep,
        backoff=deps.backoff,
    )


def make_execute_node(deps: AgentDeps) -> Any:
    """The graph node: runs the step at `current_idx`, one step per visit (so each step is
    its own traceable visit). Returns only NEW completed actions (the state appends them),
    and the undo handle lands in state in the same update, before the next step can run.
    """

    async def execute_step(state: WorkflowState) -> dict[str, Any]:
        idx = state["current_idx"]
        plan = state["plan"]
        step = plan[idx]
        outcome = await run_step(step, idx + 1, context_for(deps, state))

        records = list(state["step_records"])
        records[idx] = outcome.record
        update: dict[str, Any] = {
            "step_records": records,
            "retries": state["retries"] + outcome.retries_used,
        }
        if outcome.ok:
            assert outcome.completed is not None
            update["completed"] = [outcome.completed]
            update["current_idx"] = idx + 1
        else:
            update["error"] = outcome.record.error
            # Steps after the failure never run; say so instead of leaving them "pending".
            for later in range(idx + 1, len(records)):
                records[later] = records[later].model_copy(update={"status": "skipped"})
        update["status"] = decide_after_step(
            ok=outcome.ok,
            is_last=idx + 1 == len(plan),
            irreversible=_is_irreversible(deps, step),
            did_work=bool(state["completed"]),
        )
        return update

    return execute_step


def _is_irreversible(deps: AgentDeps, step: PlanStep) -> bool:
    try:
        return deps.registry.must_be_last(step.tool, step.action)
    except ToolError:  # unknown tool or action: not the notification
        return False


def merge_hidden_params(
    spec: ActionSpec, params: Mapping[str, Any], inputs: Mapping[str, Any]
) -> dict[str, Any]:
    """Add UI-chosen values for the parameters the planner cannot see (page and folder ids).

    Only keys the action declares as hidden are taken, so a UI value can never override what
    the plan chose for a visible parameter. Absent keys stay absent: the adapter then falls
    back to the default from settings.
    """
    merged = dict(params)
    for key in spec.planner_hidden_params:
        if inputs.get(key):
            merged[key] = inputs[key]
    return merged


async def run_step(step: PlanStep, position: int, ctx: StepContext) -> StepOutcome:
    """Execute `step` (1-based `position` in the plan). Returns an outcome; never raises."""
    record = StepRecord(step=step, status="running")
    ctx.bus.publish(
        ctx.run_id,
        "step_started",
        step_id=step.id,
        position=position,
        tool=step.tool,
        action=step.action,
        description=step.description,
    )
    try:
        spec = ctx.registry.spec(step.tool, step.action)
        params = merge_hidden_params(spec, step.params, ctx.inputs)
        return await _attempt_loop(step, position, ctx, spec, params, record)
    except ToolError as error:  # e.g. an unknown tool or action
        return _fail(ctx, record, str(error))
    except Exception as error:  # noqa: BLE001 - a bug must still lead to rollback
        # Log the type only: the message could contain user text.
        logger.error("Unexpected error in step %s: %s", step.id, type(error).__name__)
        return _fail(ctx, record, f"Unexpected internal error ({type(error).__name__})")


async def _attempt_loop(
    step: PlanStep,
    position: int,
    ctx: StepContext,
    spec: ActionSpec,
    params: dict[str, Any],
    record: StepRecord,
) -> StepOutcome:
    attempt = 0
    while True:
        attempt += 1
        record.attempts = attempt
        try:
            if ctx.injection:
                ctx.injection.check(step.id, position, attempt)
            result = await ctx.registry.execute(step.tool, step.action, params)
        except TransientToolError as error:
            retries_used = attempt - 1
            if retries_used >= MAX_RETRIES:
                return _fail(
                    ctx,
                    record,
                    f"{error} (gave up after {attempt} attempts)",
                    retries_used,
                )
            if not is_safe_to_retry(error, spec):
                # The request may already have taken effect (e.g. a Notion page was created
                # but the reply was lost); repeating it could make a duplicate.
                return _fail(
                    ctx,
                    record,
                    f"{error} (not retried: the action may already have run, "
                    "and repeating it could create a duplicate)",
                    retries_used,
                )
            delay = ctx.backoff(retries_used + 1)
            ctx.bus.publish(
                ctx.run_id,
                "step_retry",
                step_id=step.id,
                attempt=attempt + 1,
                delay_seconds=round(delay, 2),
                reason=str(error),
            )
            await ctx.sleep(delay)
            continue

        record.status = "succeeded"
        record.summary, record.url = result.summary, result.url
        ctx.bus.publish(
            ctx.run_id,
            "step_succeeded",
            step_id=step.id,
            summary=result.summary,
            url=result.url,
            attempts=attempt,
        )
        completed = CompletedAction(
            step_id=step.id,
            tool=step.tool,
            action=step.action,
            summary=result.summary,
            url=result.url,
            undo_handle=result.undo_handle,
        )
        return StepOutcome(
            ok=True, record=record, completed=completed, retries_used=attempt - 1
        )


def _fail(
    ctx: StepContext, record: StepRecord, message: str, retries_used: int = 0
) -> StepOutcome:
    record.status, record.error = "failed", message
    ctx.bus.publish(
        ctx.run_id,
        "step_failed",
        step_id=record.step.id,
        error=message,
        attempts=record.attempts,
    )
    return StepOutcome(ok=False, record=record, retries_used=retries_used)
