"""Read-only endpoints for finished and running runs: one run, the history list, the report file."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from app.api.deps import AppState, get_state
from app.store.runs import RunRecord

router = APIRouter(prefix="/api")

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
PREVIEW_CHARS = 120
NOT_FOUND = "That run does not exist."


class RunListItem(BaseModel):
    run_id: str
    status: str
    created_at: str
    finished_at: str | None
    request_preview: str
    headline: str | None
    title: str | None
    duration_seconds: float | None


def _preview(request: str) -> str:
    text = " ".join(request.split())  # one line, however the request was laid out
    return text if len(text) <= PREVIEW_CHARS else text[: PREVIEW_CHARS - 1] + "…"


@router.get("/runs")
async def list_runs(
    state: Annotated[AppState, Depends(get_state)],
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> list[RunListItem]:
    """The run history, newest first."""
    return [
        RunListItem(
            run_id=s.run_id,
            status=s.status,
            created_at=s.created_at,
            finished_at=s.finished_at,
            request_preview=_preview(s.request),
            headline=s.headline,
            title=s.title,
            duration_seconds=s.duration_seconds,
        )
        for s in await state.store.list_summaries(limit)
    ]


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str, state: Annotated[AppState, Depends(get_state)]
) -> RunRecord:
    """One run. While it is still running, `report` is null."""
    run = await state.store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return run


@router.get("/runs/{run_id}/report")
async def download_report(
    run_id: str, state: Annotated[AppState, Depends(get_state)]
) -> Response:
    """The finished run's report as a JSON file."""
    run = await state.store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if run.report is None:
        raise HTTPException(
            status_code=409,
            detail="This run has not finished yet, so there is no report to download.",
        )
    return Response(
        content=json.dumps(run.report, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="workflowweaver-report-{run_id}.json"'
        },
    )
