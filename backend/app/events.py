"""Run events: how nodes tell the outside world what the agent is doing.

Nodes call `bus.publish(...)`; they never touch the SSE stream. Phase 4's SSE endpoint just
iterates `bus.subscribe(run_id)`. The bus keeps each run's history, so a browser that
connects late (or refreshes) first receives everything it missed, then live events.

The event names below are the contract with the frontend; keep the list short.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.clock import utc_now

EventType = Literal[
    "run_started",
    "plan_ready",
    "step_started",
    "step_succeeded",
    "step_retry",
    "step_failed",
    "verify_started",
    "verify_step",
    "rollback_started",
    "rollback_step",
    "run_completed",  # always the last event; carries the final status
]
TERMINAL_EVENT: EventType = "run_completed"


def _now() -> str:
    return utc_now().isoformat()


class RunEvent(BaseModel):
    run_id: str
    seq: int  # 1, 2, 3... per run; lets a client detect gaps and order events
    type: EventType
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=_now)


class _RunChannel:
    def __init__(self) -> None:
        self.history: list[RunEvent] = []
        self.queues: list[asyncio.Queue[RunEvent]] = []

    @property
    def finished(self) -> bool:
        return bool(self.history) and self.history[-1].type == TERMINAL_EVENT


class EventBus:
    def __init__(self) -> None:
        self._channels: dict[str, _RunChannel] = {}

    def _channel(self, run_id: str) -> _RunChannel:
        return self._channels.setdefault(run_id, _RunChannel())

    def publish(self, run_id: str, type: EventType, **data: Any) -> RunEvent:
        channel = self._channel(run_id)
        event = RunEvent(
            run_id=run_id, seq=len(channel.history) + 1, type=type, data=data
        )
        channel.history.append(event)
        for queue in channel.queues:
            queue.put_nowait(event)
        return event

    def open(self, run_id: str) -> None:
        """Register a run before its first event, so `has_run` is true from the moment it starts."""
        self._channel(run_id)

    def has_run(self, run_id: str) -> bool:
        """True while the bus still holds this run (it is forgotten once stored in SQLite)."""
        return run_id in self._channels

    def history(self, run_id: str) -> list[RunEvent]:
        return list(self._channel(run_id).history)

    async def subscribe(self, run_id: str) -> AsyncIterator[RunEvent]:
        """Replay what already happened, then stream live events until the run completes."""
        channel = self._channel(run_id)
        queue: asyncio.Queue[RunEvent] = asyncio.Queue()
        # No await between taking the snapshot and registering the queue, so an event can
        # neither be missed nor delivered twice.
        replay = list(channel.history)
        already_finished = channel.finished
        channel.queues.append(queue)
        try:
            for event in replay:
                yield event
            if already_finished:
                return
            while True:
                event = await queue.get()
                yield event
                if event.type == TERMINAL_EVENT:
                    return
        finally:
            channel.queues.remove(queue)

    def discard(self, run_id: str) -> None:
        """Forget a run's history (memory hygiene once it is stored in SQLite)."""
        self._channels.pop(run_id, None)
