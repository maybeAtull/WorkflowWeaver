"""The services every endpoint shares, created once at startup and reached through `app.state`.

Endpoints never build their own bus or tracer: one bus means every run's events go to one
place, and one tracer means one Langfuse client that is flushed once, on shutdown.
"""

from dataclasses import dataclass

from fastapi import Request

from app.api.run_manager import RunManager
from app.config import Settings
from app.events import EventBus
from app.observability.tracing import Tracer
from app.store.runs import RunStore
from app.templates import Template


@dataclass
class AppState:
    settings: Settings
    bus: EventBus
    tracer: Tracer
    store: RunStore
    runs: RunManager
    templates: list[Template]


def get_state(request: Request) -> AppState:
    """The shared services, for use as a FastAPI dependency."""
    state: AppState = request.app.state.services
    return state
