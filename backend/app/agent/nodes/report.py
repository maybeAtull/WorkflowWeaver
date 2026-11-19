"""The report node: the last stop on every path. Builds the run's report and ends the event stream.

Minimal for now (status, per-step results, retries, rollback); Task 2.7 enriches it.
"""

from typing import Any

from app.agent.deps import AgentDeps
from app.agent.state import WorkflowState


def make_report_node(deps: AgentDeps) -> Any:
    async def write_report(state: WorkflowState) -> dict[str, Any]:
        report: dict[str, Any] = {
            "run_id": state["run_id"],
            "status": state["status"],
            "error": state.get("error"),
            "retries": state["retries"],
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
                for r in state["step_records"]
            ],
            "checks": [c.model_dump() for c in state["checks"]],
            "rollback": [r.model_dump() for r in state["rollbacks"]],
        }
        # The last event of every run: it tells the browser the stream is over.
        deps.bus.publish(
            state["run_id"],
            "run_completed",
            status=state["status"],
            error=state.get("error"),
        )
        return {"report": report}

    return write_report
