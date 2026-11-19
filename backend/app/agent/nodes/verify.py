"""The verify node: read the created items back from their services before announcing success.

It runs once, while everything is still undoable: just before the irreversible notification (or
at the end of a plan without one). If it ran after the message went out, a missing item could not
be un-announced. Three outcomes per item:
  - verified:    confirmed, carry on.
  - failed:      definitely wrong (missing, in the trash, or the service refused the read):
                 roll back. Erring this way costs a rerun; a false pass would announce
                 something untrue in a message we cannot recall.
  - unconfirmed: we could not tell (reads kept timing out, a listing was cut off). That is not
                 evidence the work failed, so the run goes on and the report says so.
"""

import logging
from typing import Any

from app.agent.deps import AgentDeps
from app.agent.nodes.execute_step import StepContext, context_for
from app.agent.retry import MAX_RETRIES
from app.agent.state import CompletedAction, VerifyRecord, WorkflowState
from app.tools.base import ToolError, TransientToolError

logger = logging.getLogger(__name__)


def make_verify_node(deps: AgentDeps) -> Any:
    async def verify_work(state: WorkflowState) -> dict[str, Any]:
        ctx = context_for(deps, state)
        # Only actions with an undo handle created something that can be read back.
        to_check = [a for a in state["completed"] if a.undo_handle is not None]
        deps.bus.publish(state["run_id"], "verify_started", total=len(to_check))

        records: list[VerifyRecord] = []
        for action in to_check:
            record = await _check(action, ctx)
            records.append(record)
            deps.bus.publish(
                state["run_id"],
                "verify_step",
                step_id=record.step_id,
                tool=record.tool,
                action=record.action,
                status=record.status,
                detail=record.detail,
            )

        update: dict[str, Any] = {"checks": records, "verified": True}
        failed = [r for r in records if r.status == "failed"]
        if failed:
            by_id = {a.step_id: a for a in to_check}
            update["error"] = "; ".join(
                f"{by_id[r.step_id].summary}: {r.detail}" for r in failed[:3]
            )
            update["status"] = "rolling_back"
            # The irreversible step never runs; say so instead of leaving it "pending".
            update["step_records"] = [
                (
                    r.model_copy(update={"status": "skipped"})
                    if r.status == "pending"
                    else r
                )
                for r in state["step_records"]
            ]
        else:
            more_to_run = state["current_idx"] < len(state["plan"])
            update["status"] = "running" if more_to_run else "completed"
        return update

    return verify_work


async def _check(action: CompletedAction, ctx: StepContext) -> VerifyRecord:
    assert action.undo_handle is not None

    def record(status: str, detail: str) -> VerifyRecord:
        return VerifyRecord(
            step_id=action.step_id,
            tool=action.tool,
            action=action.action,
            status=status,  # type: ignore[arg-type]
            detail=detail,
        )

    retries = 0
    while True:
        try:
            result = await ctx.registry.verify(action.undo_handle)
            return record(result.status, result.detail)
        except TransientToolError as error:
            # A read is always safe to repeat. If it keeps failing we simply cannot tell.
            if retries >= MAX_RETRIES:
                return record("unconfirmed", f"could not be read back ({error})")
            retries += 1
            await ctx.sleep(ctx.backoff(retries))
        except ToolError as error:
            return record("failed", str(error))
        except Exception as error:  # noqa: BLE001
            # A bug must not let an unchecked item through. Type only: the message could
            # contain user text.
            logger.error(
                "Unexpected error verifying %s: %s",
                action.step_id,
                type(error).__name__,
            )
            return record(
                "failed", f"Unexpected internal error ({type(error).__name__})"
            )
