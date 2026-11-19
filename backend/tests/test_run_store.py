"""The run store against a real SQLite file (no mocks): what is saved is what comes back."""

import asyncio
from pathlib import Path

import pytest

from app.config import BACKEND_DIR, Settings
from app.events import RunEvent
from app.store.runs import INTERRUPTED_HEADLINE, RunStore
from tests.test_config import FAKE_ENV


@pytest.fixture
def store(tmp_path: Path) -> RunStore:
    created = RunStore(tmp_path / "nested" / "runs.db")  # the folder does not exist yet
    created.open()
    return created


def event(
    run_id: str, seq: int, kind: str = "step_started", **data: object
) -> RunEvent:
    return RunEvent(run_id=run_id, seq=seq, type=kind, data=dict(data))  # type: ignore[arg-type]


async def test_a_new_run_is_running_and_has_its_request(store: RunStore) -> None:
    await store.create_run("r1", "Put the notes in Notion")
    run = await store.get_run("r1")
    assert run is not None
    assert (run.status, run.request, run.finished_at, run.report) == (
        "running",
        "Put the notes in Notion",
        None,
        None,
    )


async def test_an_unknown_run_is_none(store: RunStore) -> None:
    assert await store.get_run("nope") is None


async def test_finishing_a_run_saves_status_report_and_link(store: RunStore) -> None:
    await store.create_run("r1", "req")
    report = {"headline": "Done", "created": [{"url": "https://example.test/p"}]}
    await store.finish_run("r1", "completed", report, "https://langfuse.test/t/1")
    run = await store.get_run("r1")
    assert run is not None
    assert run.status == "completed" and run.report == report
    assert run.trace_url == "https://langfuse.test/t/1" and run.finished_at


async def test_a_non_final_status_is_refused(store: RunStore) -> None:
    await store.create_run("r1", "req")
    with pytest.raises(ValueError, match="not a final run status"):
        await store.finish_run("r1", "running", {})


async def test_history_lists_newest_first_and_respects_the_limit(
    store: RunStore,
) -> None:
    for name in ("a", "b", "c"):
        await store.create_run(name, f"request {name}")
        await asyncio.sleep(0.002)  # distinct timestamps
    assert [r.run_id for r in await store.list_runs()] == ["c", "b", "a"]
    assert [r.run_id for r in await store.list_runs(limit=2)] == ["c", "b"]


async def test_events_come_back_in_order_with_their_data(store: RunStore) -> None:
    await store.create_run("r1", "req")
    await store.append_event(event("r1", 2, "step_succeeded", url="https://x.test/é"))
    await store.append_event(event("r1", 1, "run_started", request="req"))
    events = await store.get_events("r1")
    assert [e.seq for e in events] == [1, 2]
    assert events[1].type == "step_succeeded"
    assert events[1].data == {"url": "https://x.test/é"}
    assert events[0].timestamp  # kept, not regenerated


async def test_replay_can_resume_after_a_sequence_number(store: RunStore) -> None:
    await store.create_run("r1", "req")
    for seq in (1, 2, 3):
        await store.append_event(event("r1", seq))
    assert [e.seq for e in await store.get_events("r1", after_seq=1)] == [2, 3]
    assert await store.get_events("r1", after_seq=3) == []


async def test_events_belong_to_one_run(store: RunStore) -> None:
    await store.create_run("r1", "a")
    await store.create_run("r2", "b")
    await store.append_event(event("r1", 1))
    assert await store.get_events("r2") == []


async def test_a_duplicate_event_is_an_error_not_silently_ignored(
    store: RunStore,
) -> None:
    import sqlite3

    await store.create_run("r1", "req")
    await store.append_event(event("r1", 1))
    with pytest.raises(sqlite3.IntegrityError):
        await store.append_event(event("r1", 1))


async def test_an_event_for_an_unknown_run_is_refused(store: RunStore) -> None:
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        await store.append_event(event("ghost", 1))


async def test_a_restart_settles_runs_left_running(store: RunStore) -> None:
    await store.create_run("dead", "was running")
    await store.create_run("done", "finished")
    await store.finish_run("done", "completed", {"headline": "ok"})
    assert await store.recover_interrupted() == 1
    dead = await store.get_run("dead")
    assert dead is not None and dead.status == "failed" and dead.finished_at
    assert dead.report is not None and dead.report["headline"] == INTERRUPTED_HEADLINE
    done = await store.get_run("done")
    assert done is not None and done.status == "completed"  # untouched
    assert await store.recover_interrupted() == 0  # nothing left to settle


async def test_data_survives_a_new_store_on_the_same_file(store: RunStore) -> None:
    await store.create_run("r1", "req")
    await store.append_event(event("r1", 1))
    reopened = RunStore(store._path)
    reopened.open()  # opening an existing database must not wipe it
    assert (await reopened.get_run("r1")) is not None
    assert len(await reopened.get_events("r1")) == 1


async def test_many_concurrent_writers_all_land(store: RunStore) -> None:
    await store.create_run("r1", "req")
    await asyncio.gather(*(store.append_event(event("r1", n)) for n in range(1, 41)))
    assert [e.seq for e in await store.get_events("r1")] == list(range(1, 41))


# ------------------------------------------------------------------ the setting


def settings_with(monkeypatch: pytest.MonkeyPatch, **extra: object) -> Settings:
    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None, **extra)  # type: ignore[arg-type]


def test_the_database_defaults_to_a_file_under_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = settings_with(monkeypatch).database_file
    assert path == (BACKEND_DIR / "data" / "workflowweaver.db").resolve()


def test_an_absolute_database_path_is_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "x.db"
    assert settings_with(monkeypatch, database_path=target).database_file == target
