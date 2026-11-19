"""Starts workflow runs in the background and keeps their record in SQLite.

One run = one asyncio task. Two things happen around the agent itself:
  - a small "persister" task copies every bus event into SQLite as it is published, so the
    bus, the graph and the nodes stay unaware of storage;
  - when the run ends, its report and Langfuse link are saved and the bus forgets it. From
    then on the history and the replay come from SQLite.

A run must always END: the SSE stream waits for `run_completed`, so even an unexpected crash
publishes one, and the run is stored as failed.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from app.agent.deps import AgentDeps
from app.agent.graph import run_workflow
from app.agent.runtime import agent_runtime
from app.config import Settings
from app.events import EventBus
from app.observability.tracing import Tracer
from app.store.runs import RunStore

logger = logging.getLogger(__name__)

# Each run starts Docker containers and spends LLM tokens (Groq's free tier allows about one
# run a minute), so only a couple may be active at once.
MAX_ACTIVE_RUNS = 2

CRASH_HEADLINE = (
    "The workflow stopped because of an internal error. "
    "Check what was created before running it again."
)

# Same shape as `agent_runtime`: (settings, bus, simulate=..., tracer=...) -> deps.
RuntimeFactory = Callable[..., AbstractAsyncContextManager[AgentDeps]]


class TooManyRuns(Exception):
    """Raised when the active-run limit is reached."""


class RunManager:
    def __init__(
        self,
        settings: Settings,
        bus: EventBus,
        tracer: Tracer,
        store: RunStore,
        runtime: RuntimeFactory = agent_runtime,
        max_active: int = MAX_ACTIVE_RUNS,
    ) -> None:
        self._settings = settings
        self._bus = bus
        self._tracer = tracer
        self._store = store
        self._runtime = runtime
        self._max_active = max_active
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._starting = (
            0  # runs between the limit check and their task being registered
        )

    @property
    def active(self) -> int:
        return len(self._tasks) + self._starting

    async def start(self, request: str, simulate: str | None = None) -> str:
        """Record the run and start it. Returns at once with the run id."""
        if self.active >= self._max_active:
            raise TooManyRuns
        # Count this run before the first await, or two simultaneous requests could both
        # pass the check above.
        self._starting += 1
        try:
            run_id = uuid.uuid4().hex[:12]
            await self._store.create_run(run_id, request)
            # Open the stream now, so a browser that asks for the events a moment later
            # finds the live run instead of an empty stored one.
            self._bus.open(run_id)
            task = asyncio.create_task(
                self._run(run_id, request, simulate), name=f"run-{run_id}"
            )
            self._tasks[run_id] = task
            task.add_done_callback(lambda _: self._tasks.pop(run_id, None))
        finally:
            self._starting -= 1
        return run_id

    async def shutdown(self) -> None:
        """Stop every active run. Their Docker containers close as each task unwinds, and
        a run left 'running' in the database is settled at the next start."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------------ one run

    async def _run(self, run_id: str, request: str, simulate: str | None) -> None:
        persister = asyncio.create_task(self._persist(run_id))
        try:
            status, report = await self._execute(run_id, request, simulate)
            await persister  # ends after the last event is stored
            await self._store.finish_run(
                run_id, status, report, self._tracer.trace_url(run_id)
            )
            self._bus.discard(run_id)
        except asyncio.CancelledError:
            persister.cancel()
            raise
        except Exception as error:  # noqa: BLE001 - the run's record must not be lost
            # Type only: a message could contain the user's text.
            logger.error("Storing run %s failed: %s", run_id, type(error).__name__)
            persister.cancel()

    async def _execute(
        self, run_id: str, request: str, simulate: str | None
    ) -> tuple[str, dict[str, Any]]:
        try:
            async with self._runtime(
                self._settings, self._bus, simulate=simulate, tracer=self._tracer
            ) as deps:
                final = await run_workflow(deps, run_id, request)
            return str(final["status"]), dict(final.get("report") or {})
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - anything may escape a run
            logger.error("Run %s crashed: %s", run_id, type(error).__name__)
            return "failed", self._close_stream_after_crash(run_id, error)

    def _close_stream_after_crash(
        self, run_id: str, error: Exception
    ) -> dict[str, Any]:
        """Make sure the event stream ends, and build a minimal report."""
        published = {event.type for event in self._bus.history(run_id)}
        if "run_started" not in published:
            self._bus.publish(run_id, "run_started")
        if "run_completed" not in published:
            self._bus.publish(
                run_id,
                "run_completed",
                status="failed",
                error=type(error).__name__,
                headline=CRASH_HEADLINE,
            )
        return {
            "run_id": run_id,
            "status": "failed",
            "headline": CRASH_HEADLINE,
            "error": type(error).__name__,
        }

    async def _persist(self, run_id: str) -> None:
        """Copy the run's events into SQLite. A storage error costs history, never the run."""
        async for event in self._bus.subscribe(run_id):
            try:
                await self._store.append_event(event)
            except Exception as error:  # noqa: BLE001
                logger.error(
                    "Saving an event of run %s failed: %s", run_id, type(error).__name__
                )
