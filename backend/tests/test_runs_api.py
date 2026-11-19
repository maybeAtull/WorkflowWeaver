"""Start a run and stream it, through the real HTTP layer with a fake agent behind it."""

import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.run_manager import CRASH_HEADLINE
from app.config import Settings
from app.main import create_app
from app.observability.tracing import InMemoryTracer
from app.store.runs import INTERRUPTED_HEADLINE, RunStore
from tests.fake_runtime import fake_runtime
from tests.test_config import FAKE_ENV


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None, database_path=tmp_path / "api.db")  # type: ignore[call-arg]


def read_stream(
    client: TestClient, run_id: str, **headers: str
) -> list[dict[str, str]]:
    """Every SSE message of a run, as {event, id, data}, until the server ends the stream."""
    messages: list[dict[str, str]] = []
    with client.stream(
        "GET", f"/api/runs/{run_id}/events", headers=headers
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        current: dict[str, str] = {}
        for line in response.iter_lines():
            if not line:
                if current:
                    messages.append(current)
                    current = {}
            elif not line.startswith(":"):  # ":" lines are keepalive comments
                name, _, value = line.partition(": ")
                current[name] = value
        if current:
            messages.append(current)
    return messages


def wait_until_stored(store: RunStore, run_id: str) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        run = store._get_run(run_id)
        if run is not None and run.status != "running":
            return
        time.sleep(0.02)
    raise AssertionError("the run was never finished in the store")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, InMemoryTracer(), fake_runtime())) as c:
        yield c


def start(client: TestClient, request: str = "Do the thing", **extra: str) -> str:
    response = client.post("/api/runs", json={"request": request, **extra})
    assert response.status_code == 202, response.text
    run_id: str = response.json()["run_id"]
    return run_id


# ------------------------------------------------------------------ start and stream


def test_a_run_streams_its_events_in_order_and_ends_with_run_completed(
    client: TestClient,
) -> None:
    run_id = start(client)
    messages = read_stream(client, run_id)
    types = [m["event"] for m in messages]
    assert types[0] == "run_started" and types[-1] == "run_completed"
    assert [int(m["id"]) for m in messages] == list(range(1, len(messages) + 1))
    done = json.loads(messages[-1]["data"])
    assert done["run_id"] == run_id and done["data"]["status"] == "completed"


def test_the_finished_run_is_stored_with_its_report_and_trace_link(
    client: TestClient,
) -> None:
    run_id = start(client)
    read_stream(client, run_id)
    store: RunStore = client.app.state.services.store  # type: ignore[attr-defined]
    wait_until_stored(store, run_id)
    run = store._get_run(run_id)
    assert run is not None
    assert run.status == "completed" and run.request == "Do the thing"
    assert run.report is not None and run.report["status"] == "completed"
    assert run.trace_url == f"memory://trace/{run_id}"
    assert len(store._get_events(run_id, 0)) == len(read_stream(client, run_id))


def test_a_finished_run_replays_from_the_store_and_the_bus_forgets_it(
    client: TestClient,
) -> None:
    run_id = start(client)
    live = read_stream(client, run_id)
    state = client.app.state.services  # type: ignore[attr-defined]
    wait_until_stored(state.store, run_id)
    assert not state.bus.has_run(run_id)
    assert read_stream(client, run_id) == live


def test_last_event_id_resumes_without_repeating_events(client: TestClient) -> None:
    run_id = start(client)
    everything = read_stream(client, run_id)
    resumed = read_stream(client, run_id, **{"Last-Event-ID": "2"})
    assert resumed == everything[2:]
    assert read_stream(client, run_id, **{"Last-Event-ID": "junk"}) == everything


def test_a_second_listener_gets_the_same_events(client: TestClient) -> None:
    run_id = start(client)
    assert read_stream(client, run_id) == read_stream(client, run_id)


def test_the_simulate_option_is_accepted(client: TestClient) -> None:
    run_id = start(client, simulate="s2:transient")
    assert read_stream(client, run_id)[-1]["event"] == "run_completed"


# ------------------------------------------------------------------ bad requests


def test_an_unknown_run_is_a_404(client: TestClient) -> None:
    response = client.get("/api/runs/nope/events")
    assert response.status_code == 404
    assert "does not exist" in response.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [
        {"request": ""},
        {"request": "   \n "},
        {"request": "x" * 6001},
        {"request": "ok", "simulate": "s2:explode"},
        {"request": "ok", "simulate": "s2; drop table"},
        {},
    ],
)
def test_invalid_requests_are_rejected_before_anything_starts(
    client: TestClient, body: dict[str, str]
) -> None:
    assert client.post("/api/runs", json=body).status_code == 422
    assert client.app.state.services.runs.active == 0  # type: ignore[attr-defined]


def test_the_request_text_is_trimmed(client: TestClient) -> None:
    run_id = start(client, request="  hello  ")
    read_stream(client, run_id)
    store: RunStore = client.app.state.services.store  # type: ignore[attr-defined]
    run = store._get_run(run_id)
    assert run is not None and run.request == "hello"


# ------------------------------------------------------------------ limits and failures


def test_too_many_active_runs_get_a_plain_429(settings: Settings) -> None:
    gate = threading.Event()
    app = create_app(settings, InMemoryTracer(), fake_runtime(gate=gate))
    with TestClient(app) as client:
        first, second = start(client), start(client)
        refused = client.post("/api/runs", json={"request": "one more"})
        assert refused.status_code == 429
        assert refused.headers["retry-after"] == "30"
        assert "try again" in refused.json()["detail"]
        gate.set()
        for run_id in (first, second):
            read_stream(client, run_id)
        wait_until_stored(client.app.state.services.store, first)  # type: ignore[attr-defined]
        deadline = time.monotonic() + 5
        while client.app.state.services.runs.active and time.monotonic() < deadline:  # type: ignore[attr-defined]
            time.sleep(0.02)
        assert start(client)  # a slot is free again


def test_a_crashing_run_still_ends_its_stream_and_is_stored_as_failed(
    settings: Settings,
) -> None:
    secret_text = "token=abc123-should-never-appear"
    app = create_app(
        settings, InMemoryTracer(), fake_runtime(crash=RuntimeError(secret_text))
    )
    with TestClient(app) as client:
        run_id = start(client)
        messages = read_stream(client, run_id)
        wait_until_stored(client.app.state.services.store, run_id)  # type: ignore[attr-defined]
        run = client.app.state.services.store._get_run(run_id)  # type: ignore[attr-defined]
    assert [m["event"] for m in messages] == ["run_started", "run_completed"]
    done = json.loads(messages[-1]["data"])["data"]
    assert done["status"] == "failed" and done["headline"] == CRASH_HEADLINE
    assert done["error"] == "RuntimeError"
    assert run.status == "failed" and run.report["headline"] == CRASH_HEADLINE
    assert secret_text not in json.dumps(messages) + run.model_dump_json()


def test_shutdown_stops_an_active_run_and_the_next_start_settles_it(
    settings: Settings,
) -> None:
    gate = threading.Event()  # never released: the run is still planning at shutdown
    app = create_app(settings, InMemoryTracer(), fake_runtime(gate=gate))
    with TestClient(app) as client:
        run_id = start(client)
    gate.set()  # lets the worker thread that was waiting on the gate finish
    store = RunStore(settings.database_file)
    stranded = store._get_run(run_id)
    assert stranded is not None and stranded.status == "running"
    with TestClient(create_app(settings, InMemoryTracer(), fake_runtime())) as client:
        settled = client.app.state.services.store._get_run(run_id)  # type: ignore[attr-defined]
        assert settled.status == "failed"
        assert settled.report["headline"] == INTERRUPTED_HEADLINE
        messages = read_stream(client, run_id)
        assert messages[-1]["event"] == "run_completed"
