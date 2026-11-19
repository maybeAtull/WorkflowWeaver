"""The template endpoint and starting a run from a template."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.observability.tracing import InMemoryTracer
from app.templates import load_templates
from tests.fake_runtime import fake_runtime
from tests.test_config import FAKE_ENV
from tests.test_runs_api import read_stream, wait_until_stored


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None, database_path=tmp_path / "t.db")  # type: ignore[call-arg]


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, InMemoryTracer(), fake_runtime())) as c:
        yield c


def test_the_catalogue_lists_the_templates_without_their_request_text(
    client: TestClient,
) -> None:
    items = client.get("/api/templates").json()
    assert [i["id"] for i in items] == [
        "meeting_to_tasks",
        "sprint_planning",
        "incident_response",
    ]
    meeting = items[0]
    assert set(meeting) == {"id", "name", "description", "fields", "example"}
    assert [f["key"] for f in meeting["fields"]] == ["meeting_title", "notes"]
    assert "request_template" not in meeting
    incident = items[2]
    severity = next(f for f in incident["fields"] if f["key"] == "severity")
    assert severity["kind"] == "choice" and "Critical" in severity["options"]


@pytest.mark.parametrize("template", load_templates(), ids=lambda t: t.id)
def test_every_template_starts_a_run_with_the_rendered_request(
    client: TestClient, template: object
) -> None:
    t = template  # type: ignore[assignment]
    response = client.post(
        "/api/runs", json={"template_id": t.id, "values": t.example}  # type: ignore[attr-defined]
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    read_stream(client, run_id)
    store = client.app.state.services.store  # type: ignore[attr-defined]
    wait_until_stored(store, run_id)
    run = store._get_run(run_id)
    assert run is not None and run.status == "completed"
    assert run.request == t.render(t.example)  # type: ignore[attr-defined]


def post(client: TestClient, **body: object) -> tuple[int, object]:
    response = client.post("/api/runs", json=body)
    return response.status_code, response.json()["detail"]


def test_a_missing_field_is_refused_naming_it(client: TestClient) -> None:
    status, detail = post(client, template_id="meeting_to_tasks", values={"notes": "x"})
    assert status == 422 and detail == "Please fill in “Meeting title”."


def test_an_unknown_field_and_a_wrong_choice_are_refused(client: TestClient) -> None:
    values = {"meeting_title": "t", "notes": "n", "surprise": "!"}
    status, detail = post(client, template_id="meeting_to_tasks", values=values)
    assert status == 422 and "no field called 'surprise'" in str(detail)
    incident = {
        "incident_title": "t",
        "severity": "Apocalyptic",
        "timeline_and_actions": "x",
    }
    status, detail = post(client, template_id="incident_response", values=incident)
    assert status == 422 and "must be one of" in str(detail)


def test_a_too_long_value_is_refused(client: TestClient) -> None:
    values = {"meeting_title": "t", "notes": "n" * 4001}
    status, detail = post(client, template_id="meeting_to_tasks", values=values)
    assert status == 422 and "too long" in str(detail)


def test_an_unknown_template_is_a_404(client: TestClient) -> None:
    status, detail = post(client, template_id="nope", values={})
    assert status == 404 and detail == "That template does not exist."


def test_a_template_and_free_text_cannot_be_mixed_or_both_missing(
    client: TestClient,
) -> None:
    both = client.post(
        "/api/runs", json={"request": "hi", "template_id": "meeting_to_tasks"}
    )
    assert both.status_code == 422 and "either a request or a template" in both.text
    assert client.post("/api/runs", json={"values": {}}).status_code == 422
    values_with_text = client.post(
        "/api/runs", json={"request": "hi", "values": {"a": "b"}}
    )
    assert values_with_text.status_code == 422


def test_nothing_starts_when_the_template_input_is_refused(client: TestClient) -> None:
    post(client, template_id="meeting_to_tasks", values={})
    assert client.app.state.services.runs.active == 0  # type: ignore[attr-defined]
    assert client.get("/api/runs").json() == []
