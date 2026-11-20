"""WorkflowWeaver FastAPI application entrypoint."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agent.runtime import agent_runtime
from app.api import history as history_api
from app.api import runs as runs_api
from app.api import templates as templates_api
from app.api.deps import AppState
from app.api.run_manager import RunManager, RuntimeFactory
from app.config import Settings, get_settings
from app.events import EventBus
from app.observability.langfuse_tracer import create_tracer
from app.observability.tracing import Tracer, guarded
from app.store.runs import RunStore
from app.templates import load_templates

# Vite dev-server origins. The browser sends these as the Origin header; without a match
# the browser blocks the frontend from reading API responses (including the SSE stream).
FRONTEND_DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def create_app(
    settings: Settings | None = None,
    tracer: Tracer | None = None,
    runtime: RuntimeFactory = agent_runtime,
) -> FastAPI:
    """Build the app. Tests pass fake settings, a recording tracer and a fake agent runtime;
    production passes none of them.

    Nothing is loaded here: settings and the Langfuse client are created at startup, so
    importing this module never needs credentials (the health check and tooling rely on it).
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        active = settings or get_settings()
        # Guarded, so a Langfuse outage can never stop the API from starting or a run from running.
        active_tracer = guarded(tracer or create_tracer(active))
        store = RunStore(active.database_file)
        store.open()
        # A run still marked 'running' belongs to a process that died: settle it honestly
        # instead of leaving the history page showing a run that will never finish.
        await store.recover_interrupted()
        bus = EventBus()
        runs = RunManager(active, bus, active_tracer, store, runtime)
        app.state.services = AppState(
            settings=active,
            bus=bus,
            tracer=active_tracer,
            store=store,
            runs=runs,
            templates=load_templates(),  # a broken template file stops startup, loudly
        )
        try:
            yield
        finally:
            # Stop active runs first, so their traces exist before the tracer is flushed.
            await runs.shutdown()
            # Sends any traces still buffered. It is network I/O, so keep it off the event loop.
            await asyncio.to_thread(active_tracer.shutdown)

    app = FastAPI(title="WorkflowWeaver", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=FRONTEND_DEV_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def refuse_bad_input(
        _: Request, error: RequestValidationError
    ) -> JSONResponse:
        # FastAPI's default reply echoes each problem's `input`. Invalid text (a lone surrogate)
        # cannot be written back, which turned a plain 422 into a 500. Only where, what and why
        # are sent; the user's own text is not echoed.
        problems = [
            {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
            for e in error.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": problems})

    app.include_router(runs_api.router)
    app.include_router(history_api.router)
    app.include_router(templates_api.router)

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
            "database": "ok",  # startup would have failed if it could not be opened
        }

    return app


app = create_app()
