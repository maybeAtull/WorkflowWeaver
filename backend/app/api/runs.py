"""Endpoints that start a workflow and stream its progress."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, field_validator
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from app.agent.nodes.parse_intent import MAX_REQUEST_CHARS
from app.api.deps import AppState, get_state
from app.api.run_manager import TooManyRuns
from app.events import RunEvent

router = APIRouter(prefix="/api")

# Seconds between keepalive comments, so a proxy does not close a stream that is just thinking.
SSE_PING_SECONDS = 15
BUSY_RETRY_SECONDS = 30
# "s2", "2", "s2:fatal", "s2:transient": the format failure injection understands.
SIMULATE_PATTERN = r"^[A-Za-z0-9_-]{1,20}(:(fatal|transient))?$"


class StartRunRequest(BaseModel):
    request: str = Field(max_length=MAX_REQUEST_CHARS)
    simulate: str | None = Field(default=None, pattern=SIMULATE_PATTERN)

    @field_validator("request")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Describe the workflow you want to run.")
        return value.strip()


class StartRunResponse(BaseModel):
    run_id: str


@router.post("/runs", status_code=202)
async def start_run(
    body: StartRunRequest, state: Annotated[AppState, Depends(get_state)]
) -> StartRunResponse:
    try:
        run_id = await state.runs.start(body.request, body.simulate)
    except TooManyRuns:
        raise HTTPException(
            status_code=429,
            detail="Other workflows are still running. Please try again in a minute.",
            headers={"Retry-After": str(BUSY_RETRY_SECONDS)},
        ) from None
    return StartRunResponse(run_id=run_id)


def _sse(event: RunEvent) -> ServerSentEvent:
    # `id` is the sequence number: the browser sends it back as Last-Event-ID on reconnect.
    return ServerSentEvent(
        data=event.model_dump_json(), event=event.type, id=str(event.seq)
    )


def _after(last_event_id: str | None) -> int:
    try:
        return max(int(last_event_id or 0), 0)
    except ValueError:
        return 0


@router.get("/runs/{run_id}/events")
async def stream_events(
    run_id: str,
    state: Annotated[AppState, Depends(get_state)],
    last_event_id: Annotated[str | None, Header()] = None,
) -> EventSourceResponse:
    """Replay what a run has already done, then follow it live until it ends."""
    if await state.store.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="That run does not exist.")
    after = _after(last_event_id)

    async def events() -> AsyncIterator[ServerSentEvent]:
        # No await between this check and `subscribe` starting, so the run cannot move from
        # live to stored in between. A live run replays from memory; a finished one from SQLite.
        if state.bus.has_run(run_id):
            async for event in state.bus.subscribe(run_id):
                if event.seq > after:
                    yield _sse(event)
        else:
            for event in await state.store.get_events(run_id, after):
                yield _sse(event)

    return EventSourceResponse(events(), ping=SSE_PING_SECONDS)
