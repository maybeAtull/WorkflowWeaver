"""A stand-in for `agent_runtime`: the real graph with fake tools, so API tests touch no service.

It yields real `AgentDeps` (the same ones the graph tests use) wired to the bus and tracer the
app passes in. `gate` holds the run at the planning step until the test releases it, which is
how tests keep a run "active" on purpose.
"""

import asyncio
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.agent.deps import AgentDeps
from app.agent.state import PlanStep, WorkflowState
from app.config import Settings
from app.events import EventBus
from app.observability.tracing import Tracer
from tests.test_graph import Rig, make, notify


def fake_runtime(
    gate: threading.Event | None = None,
    crash: Exception | None = None,
    plan: list[PlanStep] | None = None,
) -> Any:
    steps = plan or [make("a"), notify()]

    @asynccontextmanager
    async def runtime(
        settings: Settings,
        bus: EventBus,
        *,
        simulate: str | None = None,
        tracer: Tracer | None = None,
    ) -> AsyncIterator[AgentDeps]:
        if crash is not None:
            raise crash
        rig = Rig(steps, tracer=tracer)
        rig.deps.bus = bus

        async def planner(state: WorkflowState) -> list[PlanStep]:
            # Polled, not a blocking wait in a thread: a cancelled run (shutdown) must be able
            # to stop here instead of leaving a thread that holds the event loop open.
            while gate is not None and not gate.is_set():
                await asyncio.sleep(0.01)
            return steps

        rig.deps.planner = planner
        yield rig.deps

    return runtime
