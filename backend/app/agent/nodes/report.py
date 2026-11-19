"""The report node: the last stop on every path. Builds the run's report and ends the event stream."""

from typing import Any

from app.agent.deps import AgentDeps
from app.agent.report import build_report
from app.agent.state import WorkflowState
from app.clock import utc_now


def make_report_node(deps: AgentDeps) -> Any:
    async def write_report(state: WorkflowState) -> dict[str, Any]:
        report = build_report(state, utc_now())
        # The last event of every run: it tells the browser the stream is over, and carries
        # the headline so the live view can show the outcome without fetching the report.
        deps.bus.publish(
            state["run_id"],
            "run_completed",
            status=report["status"],
            error=report["error"],
            headline=report["headline"],
        )
        return {"report": report}

    return write_report
