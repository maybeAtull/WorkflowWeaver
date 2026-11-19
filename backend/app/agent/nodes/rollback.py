"""Rollback: undo everything a run already did, newest first, after a fatal failure.

Plain logic like the executor (no LLM, never raises). Two rules shape it:
  - One failed undo must not stop the others: cleaning up as much as possible beats giving up.
  - Failures are reported, never hidden, so the user knows exactly what needs manual cleanup.
Choosing the run's final status is left to the routing (Task 2.4).
"""

import logging
from typing import Any

from pydantic import BaseModel

from app.agent.deps import AgentDeps
from app.agent.nodes.execute_step import StepContext, context_for
from app.agent.retry import MAX_RETRIES
from app.agent.routing import final_status_after_rollback
from app.agent.state import CompletedAction, RollbackRecord, WorkflowState
from app.tools.base import ToolError, TransientToolError

logger = logging.getLogger(__name__)


class RollbackResult(BaseModel):
    records: list[RollbackRecord]

    @property
    def all_undone(self) -> bool:
        """True when nothing is left behind (skipped irreversible actions do not count)."""
        return all(record.status != "failed" for record in self.records)


def make_rollback_node(deps: AgentDeps) -> Any:
    """The graph node: undo everything done so far and set the final status."""

    async def roll_back(state: WorkflowState) -> dict[str, Any]:
        result = await run_rollback(state["completed"], context_for(deps, state))
        return {
            "rollbacks": result.records,  # appended to state by its reducer
            "status": final_status_after_rollback(state["completed"], result.records),
        }

    return roll_back


async def run_rollback(
    completed: list[CompletedAction], ctx: StepContext
) -> RollbackResult:
    """Undo `completed` in reverse order. Returns one record per action; never raises."""
    ctx.bus.publish(
        ctx.run_id,
        "rollback_started",
        total=len(completed),
        to_undo=sum(1 for action in completed if action.undo_handle),
    )
    records: list[RollbackRecord] = []
    for action in reversed(completed):
        record = await _undo_one(action, ctx)
        records.append(record)
        ctx.bus.publish(
            ctx.run_id,
            "rollback_step",
            step_id=record.step_id,
            tool=record.tool,
            action=record.action,
            status=record.status,
            detail=record.detail,
        )
    return RollbackResult(records=records)


async def _undo_one(action: CompletedAction, ctx: StepContext) -> RollbackRecord:
    def record(status: str, detail: str) -> RollbackRecord:
        return RollbackRecord(
            step_id=action.step_id,
            tool=action.tool,
            action=action.action,
            status=status,  # type: ignore[arg-type]
            detail=detail,
        )

    if action.undo_handle is None:
        return record(
            "skipped", "cannot be undone (for example a message already sent)"
        )
    try:
        await _undo_with_retry(action, ctx)
    except ToolError as error:
        return record("failed", str(error))
    except Exception as error:  # noqa: BLE001 - one bad undo must not stop the others
        # Type only: the message could contain user text.
        logger.error(
            "Unexpected error undoing %s: %s", action.step_id, type(error).__name__
        )
        return record("failed", f"Unexpected internal error ({type(error).__name__})")
    return record("undone", f"undone: {action.summary}")


async def _undo_with_retry(action: CompletedAction, ctx: StepContext) -> None:
    """Retry temporary failures. Always safe: an undo (move to trash) can be repeated, so
    unlike a create it does not matter whether the first attempt already took effect."""
    assert action.undo_handle is not None
    retries = 0
    while True:
        try:
            await ctx.registry.undo(action.undo_handle)
            return
        except TransientToolError:
            if retries >= MAX_RETRIES:
                raise
            retries += 1
            await ctx.sleep(ctx.backoff(retries))
