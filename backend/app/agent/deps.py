"""What the graph's nodes need from the outside world, passed in rather than imported.

Keeping the registry, event bus and planner here (not in the state) means the state stays plain
JSON-serializable data, and tests can swap in fakes without patching anything.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.agent.failure_injection import Injection
from app.agent.retry import backoff_delay
from app.agent.state import Intent, PlanStep, WorkflowState
from app.events import EventBus
from app.tools.registry import ToolRegistry

# Turns the user's request into ordered steps. The LLM planner (Task 2.6) is one
# implementation; tests use fixed plans. It may raise PlanningError with a message that is
# safe to show the user.
Planner = Callable[[WorkflowState], Awaitable[list[PlanStep]]]
# Reads the user's request into an Intent (Task 2.5). Same contract: may raise PlanningError.
IntentParser = Callable[[WorkflowState], Awaitable[Intent]]


@dataclass
class AgentDeps:
    registry: ToolRegistry
    bus: EventBus
    planner: Planner
    intent_parser: IntentParser
    injection: Injection | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    backoff: Callable[[int], float] = backoff_delay
