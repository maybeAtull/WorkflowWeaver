"""The LLM planner: Intent + tool catalogue -> a validated list of steps (the second LLM call).

The model's output is never trusted. Step ids are assigned here, every step is validated by
the tool registry, and the plan as a whole is checked (irreversible step last, exactly the
requested tools). A rejected plan gets ONE repair attempt with the problems listed; after that
the run ends with a plain message and no tool has been touched.
"""

import logging
from typing import Any

from pydantic import BaseModel

from app.agent.deps import Planner
from app.agent.llm_client import LlmClient, LlmError
from app.agent.nodes.plan import MAX_PLAN_STEPS, PlanningError
from app.agent.prompts import plan_human_prompt, plan_repair_prompt, plan_system_prompt
from app.agent.state import PlanStep, WorkflowState
from app.tools.base import ToolError
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

MAX_DESCRIPTION_CHARS = 200


# What the LLM returns. All fields required and no free-form dicts (strict providers reject
# both). Parameters are name/value pairs: text goes in `value`, a list parameter in `items`.
# `tool` is a plain string, not a Literal, so a wrong tool becomes a readable problem for the
# repair attempt instead of an opaque schema failure.


class PlannedParam(BaseModel):
    name: str
    value: str
    items: list[str]


class PlannedStep(BaseModel):
    tool: str
    action: str
    description: str
    params: list[PlannedParam]


class PlannedWorkflow(BaseModel):
    steps: list[PlannedStep]


def _is_list_param(schema: dict[str, Any]) -> bool:
    options = [
        schema,
        *schema.get("anyOf", []),
    ]  # Optional[list[X]] is anyOf [array, null]
    return any(option.get("type") == "array" for option in options)


def to_params(
    registry: ToolRegistry, tool: str, action: str, pairs: list[PlannedParam]
) -> dict[str, Any]:
    """Turn name/value pairs into a params dict, using the registry to tell list parameters
    from text ones. Empty text is dropped (it means "not set"), so a missing required
    parameter shows up as a validation problem instead of a blank value slipping through.
    """
    try:
        properties = registry.spec(tool, action).params_model.model_json_schema()[
            "properties"
        ]
    except ToolError:  # unknown tool or action: reported later by validate_step
        properties = {}

    params: dict[str, Any] = {}
    for pair in pairs:
        if _is_list_param(properties.get(pair.name, {})):
            entries = [item for item in pair.items if item.strip()]
            # A model that put a single entry in `value` instead of `items` is forgiven.
            params[pair.name] = entries or ([pair.value] if pair.value.strip() else [])
        elif pair.value != "":
            params[pair.name] = pair.value
    return params


def plan_problems(
    registry: ToolRegistry, steps: list[PlanStep], requested: list[str]
) -> list[str]:
    """Everything wrong with a plan, in plain words (names only, never the user's values)."""
    if not steps:
        return ["the plan has no steps"]
    problems: list[str] = []
    if len(steps) > MAX_PLAN_STEPS:
        problems.append(f"the plan has {len(steps)} steps; at most {MAX_PLAN_STEPS}")

    for number, step in enumerate(steps, start=1):
        step_problems = registry.validate_step(step.tool, step.action, step.params)
        problems.extend(f"step {number}: {p}" for p in step_problems)
        if step_problems:
            continue
        if registry.must_be_last(step.tool, step.action) and number != len(steps):
            problems.append(
                f"step {number} ({step.tool}.{step.action}) cannot be undone, so it must "
                "be the last step and there may be only one"
            )

    used = {step.tool for step in steps}
    for tool in requested:
        if tool not in used:
            problems.append(f"the request asks for {tool} but no step uses it")
    for tool in sorted(used - set(requested)):
        problems.append(f"the plan uses {tool}, which was not requested")
    return problems


def make_llm_planner(client: LlmClient, registry: ToolRegistry) -> Planner:
    system = plan_system_prompt(registry.render_catalogue(), MAX_PLAN_STEPS)

    async def attempt(
        human: str, requested: list[str]
    ) -> tuple[list[PlanStep], list[str]]:
        try:
            workflow = await client.ask(system, human, PlannedWorkflow)
        except LlmError as error:
            raise PlanningError(str(error)) from error
        steps = [
            PlanStep(
                id=f"s{number}",  # assigned here: the model cannot duplicate or mangle ids
                tool=planned.tool,
                action=planned.action,
                params=to_params(
                    registry, planned.tool, planned.action, planned.params
                ),
                description=planned.description.strip()[:MAX_DESCRIPTION_CHARS],
            )
            for number, planned in enumerate(workflow.steps, start=1)
        ]
        return steps, plan_problems(registry, steps, requested)

    async def plan(state: WorkflowState) -> list[PlanStep]:
        intent = state["intent"]
        if intent is None:
            raise PlanningError("Planning started without an understood request.")
        if not intent.requested_tools:
            # Guessing would mean creating things nobody asked for, so ask instead.
            raise PlanningError(
                "Tell me where the results should go: Notion, Google Drive and/or the "
                "team chat."
            )
        requested = list(intent.requested_tools)
        human = plan_human_prompt(intent)

        steps, problems = await attempt(human, requested)
        if problems:
            # Names and messages only (see plan_problems), so this is safe to log.
            logger.warning("Plan rejected, repairing: %s", "; ".join(problems))
            steps, problems = await attempt(
                plan_repair_prompt(human, problems), requested
            )
        if problems:
            logger.warning("Repaired plan still rejected: %s", "; ".join(problems))
            raise PlanningError(
                "I could not turn this request into a reliable plan. Please rephrase it "
                "or be more specific about what to create."
            )
        return steps

    return plan
