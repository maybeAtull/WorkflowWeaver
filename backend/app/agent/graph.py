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

from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agent.deps import AgentDeps
from app.agent.nodes.execute_step import make_execute_node
from app.agent.nodes.parse_intent import make_parse_intent_node
from app.agent.nodes.plan import MAX_PLAN_STEPS, make_plan_node
from app.agent.nodes.report import make_report_node
from app.agent.nodes.rollback import make_rollback_node
from app.agent.nodes.verify import make_verify_node
from app.agent.prompts import PROMPT_VERSION
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
from app.observability.tracing import TOOL_LIMIT, clip

# intent + plan + every step + verify + rollback + report, with headroom. LangGraph counts each
# node visit, so its default limit (25) would be a hidden cap; this sets it on purpose, and
# bounds a runaway loop.
RECURSION_LIMIT = MAX_PLAN_STEPS + 10


NodeFn = Callable[[WorkflowState], Awaitable[dict[str, Any]]]


def _node_input(state: WorkflowState) -> dict[str, Any]:
    """A small summary of the state for a node's span (the full state would bloat traces)."""
    return {
        "status": state.get("status"),
        "current_idx": state.get("current_idx"),
        "steps_in_plan": len(state.get("plan", [])),
    }


def _node_output(update: dict[str, Any]) -> dict[str, Any]:
    """What a node changed, in a few fields: scalars as they are, lists as counts."""
    summary = {
        key: update[key]
        for key in ("status", "error", "current_idx", "retries", "verified")
        if key in update
    }
    for key in ("completed", "rollbacks", "checks"):
        if key in update:
            summary[f"{key}_added"] = len(update[key])
    return summary


def _traced(deps: AgentDeps, name: str, node: NodeFn) -> NodeFn:
    """Run a node inside its own span. The node itself is unchanged and knows nothing of it."""

    async def wrapper(state: WorkflowState) -> dict[str, Any]:
        with deps.tracer.span(name, input=_node_input(state)) as span:
            update = await node(state)
            span.update(output=clip(_node_output(update)))
            return update

    return wrapper


def build_graph(deps: AgentDeps) -> Any:
    graph = StateGraph(WorkflowState)
    graph.add_node(
        PARSE_INTENT, _traced(deps, PARSE_INTENT, make_parse_intent_node(deps))
    )
    graph.add_node(PLAN, _traced(deps, PLAN, make_plan_node(deps)))
    graph.add_node(EXECUTE, _traced(deps, EXECUTE, make_execute_node(deps)))
    graph.add_node(VERIFY, _traced(deps, VERIFY, make_verify_node(deps)))
    graph.add_node(ROLLBACK, _traced(deps, ROLLBACK, make_rollback_node(deps)))
    graph.add_node(REPORT, _traced(deps, REPORT, make_report_node(deps)))

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

    The whole run is one trace: a root span tagged with the run id, the providers and the
    prompt version, with a span per node, tool call and LLM call nested under it. `config` is
    for LangGraph's own options; the recursion limit is always ours.
    """
    deps.bus.publish(run_id, "run_started", request=user_request)
    graph = build_graph(deps)
    context = deps.trace_context
    tags = ["workflowweaver", f"run:{run_id}"]
    tags += [
        f"{label}:{context[key]}"
        for label, key in (("llm", "llm_provider"), ("notifier", "notifier_provider"))
        if key in context
    ]
    with deps.tracer.run(
        "workflow-run",
        run_id=run_id,
        input=clip(user_request, TOOL_LIMIT),
        tags=tags,
        metadata={"run_id": run_id, "prompt_version": PROMPT_VERSION, **context},
    ) as root:
        final: WorkflowState = await graph.ainvoke(
            new_state(run_id, user_request, inputs),
            config={**(config or {}), "recursion_limit": RECURSION_LIMIT},
        )
        report = final.get("report") or {}
        root.update(
            output=clip({"status": final["status"], "headline": report.get("headline")})
        )
        # Numbers and a category that Langfuse can chart and filter runs by.
        deps.tracer.score("run_status", final["status"])
        deps.tracer.score("retries", float(final["retries"]))
        deps.tracer.score(
            "duration_seconds", float(report.get("duration_seconds", 0.0))
        )
    return final
