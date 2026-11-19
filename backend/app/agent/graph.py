"""The LangGraph that runs a workflow: plan -> execute -> verify -> (rollback) -> report.

    parse_intent -> plan_steps -> execute_step (loops once per step) -> write_report
                                      |    ^                               ^
                  just before the     v    |  (irreversible step still to come)
                  notification   verify_work -------------------------------+
                                      |
    fatal failure or a failed check --+--> roll_back --> write_report

Which edge to take is decided by `routing.py` from the run status the nodes set. Verification
sits BEFORE the irreversible notification so a failed check can still roll everything back.
"""

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agent.deps import AgentDeps
from app.agent.nodes.execute_step import make_execute_node
from app.agent.nodes.parse_intent import make_parse_intent_node
from app.agent.nodes.plan import MAX_PLAN_STEPS, make_plan_node
from app.agent.nodes.report import make_report_node
from app.agent.nodes.rollback import make_rollback_node
from app.agent.nodes.verify import make_verify_node
from app.agent.routing import (
    EXECUTE,
    PARSE_INTENT,
    PLAN,
    REPORT,
    ROLLBACK,
    VERIFY,
    route_after_intent,
    route_after_plan,
    route_after_step,
    route_after_verify,
)
from app.agent.state import WorkflowState, new_state

# intent + plan + every step + verify + rollback + report, with headroom. LangGraph counts each
# node visit, so its default limit (25) would be a hidden cap; this sets it on purpose, and
# bounds a runaway loop.
RECURSION_LIMIT = MAX_PLAN_STEPS + 10


def build_graph(deps: AgentDeps) -> Any:
    graph = StateGraph(WorkflowState)
    graph.add_node(PARSE_INTENT, make_parse_intent_node(deps))
    graph.add_node(PLAN, make_plan_node(deps))
    graph.add_node(EXECUTE, make_execute_node(deps))
    graph.add_node(VERIFY, make_verify_node(deps))
    graph.add_node(ROLLBACK, make_rollback_node(deps))
    graph.add_node(REPORT, make_report_node(deps))

    graph.add_edge(START, PARSE_INTENT)
    graph.add_conditional_edges(PARSE_INTENT, route_after_intent, [PLAN, REPORT])
    graph.add_conditional_edges(PLAN, route_after_plan, [EXECUTE, REPORT])
    graph.add_conditional_edges(
        EXECUTE, route_after_step, [EXECUTE, VERIFY, ROLLBACK, REPORT]
    )
    graph.add_conditional_edges(VERIFY, route_after_verify, [EXECUTE, ROLLBACK, REPORT])
    graph.add_edge(ROLLBACK, REPORT)
    graph.add_edge(REPORT, END)
    return graph.compile()


async def run_workflow(
    deps: AgentDeps,
    run_id: str,
    user_request: str,
    inputs: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> WorkflowState:
    """Run one workflow to its end and return the final state.

    `config` is where Phase 3 passes the Langfuse callback; the recursion limit is always ours.
    """
    deps.bus.publish(run_id, "run_started", request=user_request)
    graph = build_graph(deps)
    return await graph.ainvoke(
        new_state(run_id, user_request, inputs),
        config={**(config or {}), "recursion_limit": RECURSION_LIMIT},
    )
