"""Smoke test: ONE real intent + plan pass through the agent's own LLM client. Executes nothing.

Run from the backend/ folder:  python -m scripts.smoke_plan

Two LLM calls (about 3.5K tokens) with the synthetic "Project Falcon" notes. The tool registry
is built with a stub that fails if any tool is ever called, so no Notion page, Drive file or
chat message can be created. Prints the intent and the validated plan; never prints the key.
"""

import asyncio
import sys
from typing import Any

from app.agent.llm_client import LlmClient
from app.agent.nodes.parse_intent import make_llm_intent_parser
from app.agent.nodes.plan import PlanningError
from app.agent.planner import make_llm_planner
from app.agent.state import new_state
from app.config import get_settings
from app.llm import get_llm
from app.tools.registry import build_registry
from scripts.smoke_intent import REQUEST


class NoToolsMcp:
    """Stands in for the MCP servers: any call is a bug in this script."""

    async def call_tool(
        self, server: str, tool: str, arguments: dict[str, Any] | None = None
    ) -> str:
        raise AssertionError("the plan smoke test must not call any tool")


async def main() -> int:
    settings = get_settings()
    print(f"provider={settings.llm_provider}, model={settings.llm_model_name}")
    client = LlmClient(lambda: get_llm(settings))
    registry = build_registry(settings, NoToolsMcp())
    state = new_state("smoke", REQUEST)
    try:
        state["intent"] = await make_llm_intent_parser(client)(state)
        steps = await make_llm_planner(client, registry)(state)
    except PlanningError as error:  # carries a message that is safe to print
        print(f"[FAIL] {error}")
        return 1
    print(f"[PASS] intent parsed and a valid {len(steps)}-step plan built")
    for number, step in enumerate(steps, start=1):
        print(f"\n{number}. {step.tool}.{step.action}: {step.description}")
        print(step.model_dump_json(indent=2, include={"params"}))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
