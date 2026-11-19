"""The plan node: ask the (injected) planner for steps and set the run up to execute them.

The real LLM planner (Task 2.6) plugs in through `AgentDeps.planner`; this wrapper stays the same.
"""

import logging
from typing import Any

from app.agent.deps import AgentDeps
from app.agent.state import StepRecord, WorkflowState

logger = logging.getLogger(__name__)

# Keeps a run (and the graph's recursion limit) bounded; the planner is told the same limit.
MAX_PLAN_STEPS = 10


class PlanningError(Exception):
    """The request could not be turned into a plan. The message is safe to show the user."""


def make_plan_node(deps: AgentDeps) -> Any:
    async def plan_steps(state: WorkflowState) -> dict[str, Any]:
        try:
            steps = await deps.planner(state)
        except PlanningError as error:
            return _failed(str(error))
        except Exception as error:  # noqa: BLE001
            # A planner bug must end the run cleanly. Log the type only: the message could
            # contain the user's request text.
            logger.error("Planner crashed: %s", type(error).__name__)
            return _failed(f"Planning failed unexpectedly ({type(error).__name__})")

        if not steps:
            return _failed("The request did not lead to any steps to run.")
        if len(steps) > MAX_PLAN_STEPS:
            return _failed(
                f"The plan has {len(steps)} steps; at most {MAX_PLAN_STEPS} are allowed."
            )

        deps.bus.publish(
            state["run_id"],
            "plan_ready",
            steps=[step.model_dump() for step in steps],
        )
        return {
            "plan": steps,
            "step_records": [StepRecord(step=step) for step in steps],
            "current_idx": 0,
            "status": "running",
        }

    return plan_steps


def _failed(message: str) -> dict[str, Any]:
    return {"status": "failed", "error": message}
