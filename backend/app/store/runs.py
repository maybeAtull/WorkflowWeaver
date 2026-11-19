"""The run store: what each run was asked, what happened, and how it ended.

Every method is async and does its disk work in a worker thread. The events are the same
`RunEvent`s the SSE stream sends, so a finished run can be replayed from here after a restart.
"""

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.agent.state import FINAL_STATUSES
from app.clock import utc_now
from app.events import RunEvent
from app.store.db import connect, init_db

INTERRUPTED_HEADLINE = "The server restarted while this run was in progress."


class RunRecord(BaseModel):
    run_id: str
    request: str
    status: str
    created_at: str
    finished_at: str | None = None
    report: dict[str, Any] | None = None
    trace_url: str | None = None


class RunSummary(BaseModel):
    """One row of the history list: enough to recognise a run, without its full report."""

    run_id: str
    request: str
    status: str
    created_at: str
    finished_at: str | None = None
    headline: str | None = None
    title: str | None = None
    duration_seconds: float | None = None


def _record(row: sqlite3.Row) -> RunRecord:
    report = row["report_json"]
    return RunRecord(
        run_id=row["run_id"],
        request=row["request"],
        status=row["status"],
        created_at=row["created_at"],
        finished_at=row["finished_at"],
        report=json.loads(report) if report else None,
        trace_url=row["trace_url"],
    )


def _event(row: sqlite3.Row) -> RunEvent:
    return RunEvent(
        run_id=row["run_id"],
        seq=row["seq"],
        type=row["type"],
        data=json.loads(row["data_json"]),
        timestamp=row["timestamp"],
    )


class RunStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def open(self) -> None:
        """Create the database if needed. Called once at startup (a quick local operation)."""
        init_db(self._path)

    # Each public method below runs its blocking `_x` twin in a worker thread.

    async def create_run(self, run_id: str, request: str) -> None:
        await asyncio.to_thread(self._create_run, run_id, request)

    async def append_event(self, event: RunEvent) -> None:
        await asyncio.to_thread(self._append_event, event)

    async def finish_run(
        self,
        run_id: str,
        status: str,
        report: dict[str, Any],
        trace_url: str | None = None,
    ) -> None:
        if status not in FINAL_STATUSES:
            raise ValueError(f"{status!r} is not a final run status")
        await asyncio.to_thread(self._finish_run, run_id, status, report, trace_url)

    async def get_run(self, run_id: str) -> RunRecord | None:
        return await asyncio.to_thread(self._get_run, run_id)

    async def list_runs(self, limit: int = 50) -> list[RunRecord]:
        """Newest first."""
        return await asyncio.to_thread(self._list_runs, limit)

    async def list_summaries(self, limit: int = 20) -> list[RunSummary]:
        """Newest first, with just a few report fields (the full reports are not loaded)."""
        return await asyncio.to_thread(self._list_summaries, limit)

    async def get_events(self, run_id: str, after_seq: int = 0) -> list[RunEvent]:
        """Events with a sequence number above `after_seq`, in order (for replay)."""
        return await asyncio.to_thread(self._get_events, run_id, after_seq)

    async def recover_interrupted(self) -> int:
        """Mark runs left 'running' by a crash or restart as failed. Returns how many."""
        return await asyncio.to_thread(self._recover_interrupted)

    # ------------------------------------------------------------------ blocking work

    def _create_run(self, run_id: str, request: str) -> None:
        with closing(connect(self._path)) as db, db:
            db.execute(
                "INSERT INTO runs (run_id, request, status, created_at) VALUES (?, ?, ?, ?)",
                (run_id, request, "running", utc_now().isoformat()),
            )

    def _append_event(self, event: RunEvent) -> None:
        with closing(connect(self._path)) as db, db:
            db.execute(
                "INSERT INTO run_events (run_id, seq, type, data_json, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    event.run_id,
                    event.seq,
                    event.type,
                    json.dumps(event.data, default=str),
                    event.timestamp,
                ),
            )

    def _finish_run(
        self, run_id: str, status: str, report: dict[str, Any], trace_url: str | None
    ) -> None:
        with closing(connect(self._path)) as db, db:
            db.execute(
                "UPDATE runs SET status = ?, finished_at = ?, report_json = ?, trace_url = ? "
                "WHERE run_id = ?",
                (
                    status,
                    utc_now().isoformat(),
                    json.dumps(report, default=str),
                    trace_url,
                    run_id,
                ),
            )

    def _get_run(self, run_id: str) -> RunRecord | None:
        with closing(connect(self._path)) as db:
            row = db.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return _record(row) if row else None

    def _list_runs(self, limit: int) -> list[RunRecord]:
        with closing(connect(self._path)) as db:
            rows = db.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_record(row) for row in rows]

    def _list_summaries(self, limit: int) -> list[RunSummary]:
        with closing(connect(self._path)) as db:
            rows = db.execute(
                "SELECT run_id, request, status, created_at, finished_at, "
                "json_extract(report_json, '$.headline') AS headline, "
                "json_extract(report_json, '$.title') AS title, "
                "json_extract(report_json, '$.duration_seconds') AS duration_seconds "
                "FROM runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [RunSummary(**dict(row)) for row in rows]

    def _get_events(self, run_id: str, after_seq: int) -> list[RunEvent]:
        with closing(connect(self._path)) as db:
            rows = db.execute(
                "SELECT * FROM run_events WHERE run_id = ? AND seq > ? ORDER BY seq",
                (run_id, after_seq),
            ).fetchall()
        return [_event(row) for row in rows]

    def _recover_interrupted(self) -> int:
        report = json.dumps(
            {"status": "failed", "headline": INTERRUPTED_HEADLINE, "error": None}
        )
        now = utc_now().isoformat()
        with closing(connect(self._path)) as db, db:
            stranded = [
                row["run_id"]
                for row in db.execute(
                    "SELECT run_id FROM runs WHERE status = 'running'"
                )
            ]
            for run_id in stranded:
                # Close the run's event stream too: a replay (or a browser that reconnects)
                # waits for run_completed, and would otherwise wait forever.
                last = db.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM run_events WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]
                data = {
                    "status": "failed",
                    "error": None,
                    "headline": INTERRUPTED_HEADLINE,
                }
                db.execute(
                    "INSERT INTO run_events (run_id, seq, type, data_json, timestamp) "
                    "VALUES (?, ?, 'run_completed', ?, ?)",
                    (run_id, last + 1, json.dumps(data), now),
                )
            db.execute(
                "UPDATE runs SET status = 'failed', finished_at = ?, report_json = ? "
                "WHERE status = 'running'",
                (now, report),
            )
            return len(stranded)
