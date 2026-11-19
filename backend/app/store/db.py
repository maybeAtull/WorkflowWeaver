"""SQLite connection and schema for the run history.

Plain stdlib `sqlite3`: the load is a handful of rows per run, so no async driver is needed.
Callers run these functions in a worker thread (see runs.py) so the event loop never waits on
the disk.
"""

import sqlite3
from pathlib import Path

# Wait this long for another writer instead of failing at once with "database is locked".
BUSY_TIMEOUT_SECONDS = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    request     TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    finished_at TEXT,
    report_json TEXT,
    trace_url   TEXT
);
CREATE TABLE IF NOT EXISTS run_events (
    run_id    TEXT NOT NULL REFERENCES runs(run_id),
    seq       INTEGER NOT NULL,
    type      TEXT NOT NULL,
    data_json TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
CREATE INDEX IF NOT EXISTS runs_created_at ON runs (created_at DESC);
"""


def connect(path: Path) -> sqlite3.Connection:
    """A fresh connection. One per call keeps threads from sharing a connection."""
    connection = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(path: Path) -> None:
    """Create the folder, the file and the tables if they are missing. Safe to repeat."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(path)
    try:
        # WAL lets a reader (the history page) work while a run is writing events.
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA)
        connection.commit()
    finally:
        connection.close()
