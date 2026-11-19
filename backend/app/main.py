"""WorkflowWeaver FastAPI application entrypoint."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.deps import AppState
from app.config import Settings, get_settings
from app.events import EventBus
from app.observability.langfuse_tracer import create_tracer
from app.observability.tracing import Tracer, guarded

# Vite dev-server origins. The browser sends these as the Origin header; without a match
# the browser blocks the frontend from reading API responses (including the SSE stream).
FRONTEND_DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def create_app(
    settings: Settings | None = None, tracer: Tracer | None = None
) -> FastAPI:
    """Build the app. Tests pass fake settings and a recording tracer; production passes none.

    Nothing is loaded here: settings and the Langfuse client are created at startup, so
    importing this module never needs credentials (the health check and tooling rely on it).
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        active = settings or get_settings()
        # Guarded, so a Langfuse outage can never stop the API from starting or a run from running.
        active_tracer = guarded(tracer or create_tracer(active))
        app.state.services = AppState(
            settings=active, bus=EventBus(), tracer=active_tracer
        )
        try:
            yield
        finally:
            # Sends any traces still buffered. It is network I/O, so keep it off the event loop.
            await asyncio.to_thread(active_tracer.shutdown)

    app = FastAPI(title="WorkflowWeaver", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=FRONTEND_DEV_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """Liveness probe for the UI and evaluators. Never includes a secret."""
        services: AppState | None = getattr(app.state, "services", None)
        if services is None:  # served without startup (a bare test client)
            return {"status": "ok"}
        return {
            "status": "ok",
            "llm_provider": services.settings.llm_provider,
            "notifier_provider": services.settings.notifier_provider,
        }

    return app


app = create_app()
