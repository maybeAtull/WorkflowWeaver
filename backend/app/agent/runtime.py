"""Assembles the whole agent from the settings. The API (Phase 4) and the runner script both use it.

`agent_runtime` opens ONE MCP session manager for the run and closes it afterwards, even on an
error, so a run never leaves Docker containers behind. The servers themselves start lazily, on a
tool's first call (a few seconds each), so a run that never reaches Drive never starts Drive.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from app.agent.deps import AgentDeps
from app.agent.failure_injection import parse_injection
from app.agent.llm_client import LlmClient
from app.agent.nodes.parse_intent import make_llm_intent_parser
from app.agent.planner import make_llm_planner
from app.config import Settings
from app.events import EventBus
from app.llm import get_llm
from app.observability.tracing import NoopTracer, Tracer
from app.tools.mcp_servers import default_specs
from app.tools.mcp_session import McpSessionManager
from app.tools.notion import McpCaller
from app.tools.registry import build_registry


def build_agent_deps(
    settings: Settings,
    mcp: McpCaller,
    bus: EventBus,
    *,
    llm_client: LlmClient | None = None,
    http: httpx.AsyncClient | None = None,
    simulate: str | None = None,
    tracer: Tracer | None = None,
) -> AgentDeps:
    """Wire the registry, the LLM-backed intent parser and planner, and failure injection.

    `simulate` overrides SIMULATE_FAILURE_STEP (None means: use the setting; "" means: off).
    `tracer` records the run (None means: no tracing).
    """
    registry = build_registry(settings, mcp, http)
    client = llm_client or LlmClient(lambda: get_llm(settings), tracer=tracer)
    spec = settings.simulate_failure_step if simulate is None else simulate
    return AgentDeps(
        registry=registry,
        bus=bus,
        planner=make_llm_planner(client, registry),
        intent_parser=make_llm_intent_parser(client),
        injection=parse_injection(spec),
        tracer=tracer or NoopTracer(),
        trace_context={
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model_name,
            "notifier_provider": settings.notifier_provider,
        },
    )


@asynccontextmanager
async def agent_runtime(
    settings: Settings,
    bus: EventBus,
    *,
    llm_client: LlmClient | None = None,
    http: httpx.AsyncClient | None = None,
    simulate: str | None = None,
    tracer: Tracer | None = None,
) -> AsyncIterator[AgentDeps]:
    """Everything one run needs, with its MCP servers closed when the run ends."""
    async with McpSessionManager(default_specs(settings)) as mcp:
        yield build_agent_deps(
            settings,
            mcp,
            bus,
            llm_client=llm_client,
            http=http,
            simulate=simulate,
            tracer=tracer,
        )
