"""The run manager on its own: the active-run limit and the bookkeeping around a run."""

import asyncio
import threading
from pathlib import Path

import pytest

from app.api.run_manager import RunManager, TooManyRuns
from app.config import Settings
from app.events import EventBus
from app.observability.tracing import InMemoryTracer
from app.store.runs import RunStore
from tests.fake_runtime import fake_runtime
from tests.test_config import FAKE_ENV


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def build(
    settings: Settings, tmp_path: Path, max_active: int = 2, **runtime_options: object
) -> tuple[RunManager, RunStore, EventBus]:
    store = RunStore(tmp_path / "m.db")
    store.open()
    bus = EventBus()
    manager = RunManager(
        settings,
        bus,
        InMemoryTracer(),
        store,
        fake_runtime(**runtime_options),  # type: ignore[arg-type]
        max_active,
    )
    return manager, store, bus


async def settle(manager: RunManager) -> None:
    while manager.active:
        await asyncio.sleep(0.01)


async def test_a_started_run_is_recorded_streams_and_is_finished(
    settings: Settings, tmp_path: Path
) -> None:
    manager, store, bus = build(settings, tmp_path)
    run_id = await manager.start("Do it")
    assert (await store.get_run(run_id)) is not None  # recorded before start() returned
    assert bus.has_run(run_id)  # the stream is open from the first moment
    await settle(manager)
    run = await store.get_run(run_id)
    assert run is not None and run.status == "completed" and run.finished_at
    assert not bus.has_run(run_id)
    events = await store.get_events(run_id)
    assert events[0].type == "run_started" and events[-1].type == "run_completed"


async def test_simultaneous_starts_cannot_both_pass_the_limit(
    settings: Settings, tmp_path: Path
) -> None:
    gate = threading.Event()
    manager, _, _ = build(settings, tmp_path, max_active=1, gate=gate)
    results = await asyncio.gather(
        manager.start("a"), manager.start("b"), return_exceptions=True
    )
    assert sum(isinstance(r, TooManyRuns) for r in results) == 1
    assert sum(isinstance(r, str) for r in results) == 1
    gate.set()
    await settle(manager)


async def test_the_slot_is_freed_when_a_run_ends(
    settings: Settings, tmp_path: Path
) -> None:
    manager, _, _ = build(settings, tmp_path, max_active=1)
    await manager.start("a")
    await settle(manager)
    assert manager.active == 0
    await manager.start("b")  # does not raise
    await settle(manager)


async def test_a_failed_store_write_does_not_break_the_run(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, store, _ = build(settings, tmp_path)

    async def broken(event: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(store, "append_event", broken)
    run_id = await manager.start("Do it")
    await settle(manager)
    run = await store.get_run(run_id)
    assert run is not None and run.status == "completed"  # the run itself was fine


async def test_shutdown_cancels_active_runs(settings: Settings, tmp_path: Path) -> None:
    gate = threading.Event()
    manager, store, _ = build(settings, tmp_path, gate=gate)
    run_id = await manager.start("a")
    await asyncio.sleep(0.05)
    await manager.shutdown()
    gate.set()
    assert manager.active == 0
    run = await store.get_run(run_id)
    assert run is not None and run.status == "running"  # settled at the next startup
