"""The run report: what a business user reads at the end of a run.

A pure function of the final state (so it is testable without a graph) that returns plain JSON.
Two rules shape it:
  - Honest about what exists. `created` lists links only when the run succeeded; after a
    rollback those items are in the trash, so listing them would mislead.
  - Nothing left behind is silent. When an undo fails, the item appears in `needs_cleanup`
    with its link and the reason, so the user can remove it by hand.
"""

from datetime import datetime
from typing import Any

from app.agent.state import RollbackRecord, WorkflowState

REPORT_SCHEMA_VERSION = 1
_SUCCESS = ("completed", "completed_with_warning")


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def _needs_cleanup(state: WorkflowState) -> list[dict[str, Any]]:
    by_id = {action.step_id: action for action in state["completed"]}
    items = []
    for record in state["rollbacks"]:
        if record.status != "failed":
            continue
        action = by_id.get(record.step_id)
        items.append(
            {
                "step_id": record.step_id,
                "label": action.summary if action else f"{record.tool}.{record.action}",
                "url": action.url if action else None,
                "reason": record.detail,
            }
        )
    return items


def _headline(state: WorkflowState, needs_cleanup: list[dict[str, Any]]) -> str:
    status = state["status"]
    error = state.get("error") or "unknown reason"
    steps = len(state["step_records"])
    unconfirmed = sum(1 for c in state["checks"] if c.status == "unconfirmed")

    if status == "completed":
        base = f"Done: all {_plural(steps, 'step')} finished"
        if unconfirmed:
            return f"{base}, but {_plural(unconfirmed, 'item')} could not be confirmed."
        if state["checks"]:
            return f"{base} and the created items were confirmed."
        return f"{base}."
    if status == "completed_with_warning":
        return "Your work was saved, but the team message could not be sent."
    if status == "rolled_back":
        return f"Stopped. Everything that had been created was undone. Reason: {error}"
    if status == "failed":
        if needs_cleanup:
            return (
                f"Stopped, but {_plural(len(needs_cleanup), 'item')} could not be "
                f"removed and need manual cleanup. Reason: {error}"
            )
        if not state["completed"]:
            return f"The workflow could not run: {error}"
        return f"The workflow failed: {error}"
    return "The run did not finish."


def _duration_seconds(started_at: str, now: datetime) -> float:
    try:
        return max(
            0.0, round((now - datetime.fromisoformat(started_at)).total_seconds(), 1)
        )
    except ValueError:  # a malformed timestamp must not break the report
        return 0.0


def build_report(state: WorkflowState, now: datetime) -> dict[str, Any]:
    """The finished run as plain JSON (safe to store, stream and show)."""
    records = state["step_records"]
    intent = state.get("intent")
    needs_cleanup = _needs_cleanup(state)
    created = (
        [
            {
                "step_id": a.step_id,
                "tool": a.tool,
                "label": a.summary,
                "url": a.url,
            }
            for a in state["completed"]
            if a.url
        ]
        if state["status"] in _SUCCESS
        else []
    )
    rollback: list[RollbackRecord] = state["rollbacks"]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_id": state["run_id"],
        "status": state["status"],
        "headline": _headline(state, needs_cleanup),
        "error": state.get("error"),
        "title": intent.title if intent else None,
        "summary": intent.summary if intent else None,
        "started_at": state["started_at"],
        "finished_at": now.isoformat(),
        "duration_seconds": _duration_seconds(state["started_at"], now),
        "retries": state["retries"],
        "counts": {
            "total": len(records),
            **{
                status: sum(1 for r in records if r.status == status)
                for status in ("succeeded", "failed", "skipped", "pending")
            },
        },
        "steps": [
            {
                "id": r.step.id,
                "tool": r.step.tool,
                "action": r.step.action,
                "description": r.step.description,
                "status": r.status,
                "attempts": r.attempts,
                "summary": r.summary,
                "url": r.url,
                "error": r.error,
            }
            for r in records
        ],
        "created": created,
        "checks": [c.model_dump(mode="json") for c in state["checks"]],
        "rollback": [r.model_dump(mode="json") for r in rollback],
        "needs_cleanup": needs_cleanup,
    }
