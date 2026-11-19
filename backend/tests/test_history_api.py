"""The read-only endpoints: one run, the history list and the downloadable report."""

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
from app.store.runs import RunStore
from tests.fake_runtime import fake_runtime
from tests.test_config import FAKE_ENV
from tests.test_runs_api import read_stream, start, wait_until_stored


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None, database_path=tmp_path / "h.db")  # type: ignore[call-arg]


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, InMemoryTracer(), fake_runtime())) as c:
        yield c


def finish(client: TestClient, request: str = "Do the thing") -> str:
    run_id = start(client, request)
    read_stream(client, run_id)
    wait_until_stored(client.app.state.services.store, run_id)  # type: ignore[attr-defined]
    return run_id


# ------------------------------------------------------------------ one run


def test_a_finished_run_comes_back_with_its_report(client: TestClient) -> None:
    run_id = finish(client)
    body = client.get(f"/api/runs/{run_id}").json()
    assert body["run_id"] == run_id and body["status"] == "completed"
    assert body["request"] == "Do the thing" and body["finished_at"]
    assert body["trace_url"] == f"memory://trace/{run_id}"
    assert body["report"]["status"] == "completed" and body["report"]["headline"]


def test_a_running_run_has_no_report_yet(settings: Settings) -> None:
    gate = threading.Event()
    app = create_app(settings, InMemoryTracer(), fake_runtime(gate=gate))
    with TestClient(app) as client:
        run_id = start(client)
        body = client.get(f"/api/runs/{run_id}").json()
        assert body["status"] == "running" and body["report"] is None
        assert body["finished_at"] is None
        gate.set()
        read_stream(client, run_id)


def test_an_unknown_run_is_a_404(client: TestClient) -> None:
    assert client.get("/api/runs/nope").status_code == 404
    assert client.get("/api/runs/nope/report").status_code == 404


# ------------------------------------------------------------------ history


def test_history_is_newest_first_and_compact(client: TestClient) -> None:
    first = finish(client, "first request")
    time.sleep(0.01)
    second = finish(client, "second request")
    items = client.get("/api/runs").json()
    assert [i["run_id"] for i in items] == [second, first]
    top = items[0]
    assert top["status"] == "completed" and top["request_preview"] == "second request"
    assert top["headline"] and top["duration_seconds"] is not None
    assert "report" not in top and "request" not in top  # the full report stays out


def test_history_respects_the_limit_and_its_bounds(client: TestClient) -> None:
    for n in range(3):
        finish(client, f"request {n}")
        time.sleep(0.01)
    assert len(client.get("/api/runs?limit=2").json()) == 2
    assert client.get("/api/runs?limit=0").status_code == 422
    assert client.get("/api/runs?limit=101").status_code == 422
    assert client.get("/api/runs?limit=100").status_code == 200


def test_a_long_multiline_request_is_previewed_on_one_short_line(
    client: TestClient,
) -> None:
    finish(client, "line one\n\n   line two " + "x" * 300)
    preview = client.get("/api/runs").json()[0]["request_preview"]
    assert "\n" not in preview and preview.startswith("line one line two x")
    assert len(preview) == 120 and preview.endswith("…")


def test_an_empty_history_is_an_empty_list(client: TestClient) -> None:
    assert client.get("/api/runs").json() == []


def test_a_running_run_is_listed_without_report_fields(settings: Settings) -> None:
    gate = threading.Event()
    app = create_app(settings, InMemoryTracer(), fake_runtime(gate=gate))
    with TestClient(app) as client:
        start(client)
        item = client.get("/api/runs").json()[0]
        assert item["status"] == "running"
        assert (item["headline"], item["title"], item["duration_seconds"]) == (
            None,
            None,
            None,
        )
        gate.set()


def test_a_crashed_run_is_listed_as_failed_with_its_headline(
    settings: Settings,
) -> None:
    app = create_app(
        settings, InMemoryTracer(), fake_runtime(crash=RuntimeError("boom"))
    )
    with TestClient(app) as client:
        run_id = finish(client)
        item = client.get("/api/runs").json()[0]
    assert item["run_id"] == run_id and item["status"] == "failed"
    assert item["headline"] == CRASH_HEADLINE


# ------------------------------------------------------------------ download


def test_the_report_downloads_as_a_json_file(client: TestClient) -> None:
    run_id = finish(client)
    response = client.get(f"/api/runs/{run_id}/report")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert (
        response.headers["content-disposition"]
        == f'attachment; filename="workflowweaver-report-{run_id}.json"'
    )
    assert (
        json.loads(response.text) == client.get(f"/api/runs/{run_id}").json()["report"]
    )


def test_there_is_no_report_to_download_while_the_run_is_active(
    settings: Settings,
) -> None:
    gate = threading.Event()
    app = create_app(settings, InMemoryTracer(), fake_runtime(gate=gate))
    with TestClient(app) as client:
        run_id = start(client)
        response = client.get(f"/api/runs/{run_id}/report")
        assert response.status_code == 409
        assert "not finished" in response.json()["detail"]
        gate.set()
        read_stream(client, run_id)


def test_history_survives_a_restart(settings: Settings) -> None:
    with TestClient(create_app(settings, InMemoryTracer(), fake_runtime())) as c:
        run_id = finish(c)
    with TestClient(create_app(settings, InMemoryTracer(), fake_runtime())) as c:
        assert [i["run_id"] for i in c.get("/api/runs").json()] == [run_id]
        assert c.get(f"/api/runs/{run_id}").json()["status"] == "completed"
    assert RunStore(settings.database_file)._get_run(run_id) is not None
