"""Robustness drills (Task 6.6): awkward input, many clients, boundaries and restarts.

Each drill either proves the system copes and stays as a permanent test, or exposes a defect. A
defect is recorded as `xfail(strict=True)` with its finding number, so the suite stays green, the
gap is documented where it lives, and the test starts failing loudly (strict) the moment the gap is
fixed, which tells us to drop the marker.
"""

import asyncio
import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.run_manager import MAX_ACTIVE_RUNS
from app.config import Settings
from app.events import EventBus
from app.main import create_app
from app.observability.tracing import InMemoryTracer
from app.store.runs import RunStore
from app.tools.base import FatalToolError
from app.tools.drive import MAX_NAME_CHARS, DriveAdapter
from app.tools.notifier import NotificationMessage
from app.tools.notion import CreatePageParams, build_children
from app.tools.slack import SlackNotifier
from app.tools.teams import TeamsNotifier
from tests.fake_runtime import fake_runtime
from tests.test_config import FAKE_ENV
from tests.test_runs_api import read_stream, wait_until_stored
from tests.test_tools_drive import FakeMcp, created

URL = "https://hooks.example.invalid/services/SECRET"

# Text a real user or an LLM can produce. None of it may crash a run, a stream or a store.
AWKWARD = {
    "emoji": "Launch \U0001f680 plan \U0001f469‍\U0001f4bb — ok ✅",
    "right_to_left": "اجتماع الفريق — תכנון",
    "accents": "Café ñandú über Ångström",
    "control_chars": "tab\there\x07bell\x1b[31mred",
    "quotes_and_slashes": 'He said "hi" and \\ backslash \' single',
    "unbroken_word": "x" * 3000,
    "line_separators": "line sep para",
    "markup": "<script>alert(1)</script> & <b>bold</b> {{not_a_placeholder}}",
}


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None, database_path=tmp_path / "robust.db")  # type: ignore[call-arg]


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, InMemoryTracer(), fake_runtime())) as c:
        yield c


CR, LF = chr(13), chr(10)


def sse_events(client: TestClient, run_id: str) -> list[dict]:
    """The stream read the way the SSE standard (and so a browser) does: lines end at CR, LF or
    CRLF only. (httpx's iter_lines also splits at U+2028, which a browser does not.)"""
    with client.stream("GET", f"/api/runs/{run_id}/events") as response:
        raw = response.read().decode("utf-8")
    events = []
    normalised = raw.replace(CR + LF, LF).replace(CR, LF)
    for block in normalised.split(LF + LF):
        data = [line[6:] for line in block.split(LF) if line.startswith("data: ")]
        if data:
            events.append(json.loads(LF.join(data)))
    return events


def start(client: TestClient, request: str) -> str:
    response = client.post("/api/runs", json={"request": request})
    assert response.status_code == 202, response.text
    run_id: str = response.json()["run_id"]
    return run_id


# ------------------------------------------------------------------ many runs at once


def test_runs_at_the_same_time_stay_apart(settings: Settings) -> None:
    gate = threading.Event()
    app = create_app(settings, InMemoryTracer(), fake_runtime(gate=gate))
    with TestClient(app) as client:
        ids = [start(client, f"request number {n}") for n in range(MAX_ACTIVE_RUNS)]
        assert client.app.state.services.runs.active == MAX_ACTIVE_RUNS  # type: ignore[attr-defined]
        gate.set()
        streams = {run_id: read_stream(client, run_id) for run_id in ids}
        store = client.app.state.services.store  # type: ignore[attr-defined]
        for n, run_id in enumerate(ids):
            events = [json.loads(m["data"]) for m in streams[run_id]]
            assert {e["run_id"] for e in events} == {run_id}  # nothing from another run
            assert events[0]["data"]["request"] == f"request number {n}"
            wait_until_stored(store, run_id)
            record = store._get_run(run_id)
            assert record is not None and record.status == "completed"
            assert record.report is not None and record.report["run_id"] == run_id


def test_a_burst_of_starts_is_refused_cleanly_and_nothing_leaks(
    settings: Settings,
) -> None:
    gate = threading.Event()
    app = create_app(settings, InMemoryTracer(), fake_runtime(gate=gate))
    with TestClient(app) as client:
        statuses = [
            client.post("/api/runs", json={"request": f"r{n}"}).status_code
            for n in range(25)
        ]
        assert statuses.count(202) == MAX_ACTIVE_RUNS
        assert statuses.count(429) == 25 - MAX_ACTIVE_RUNS
        assert client.app.state.services.runs.active == MAX_ACTIVE_RUNS  # type: ignore[attr-defined]
        gate.set()
        runs = client.app.state.services.runs  # type: ignore[attr-defined]
        for _ in range(500):
            if runs.active == 0:
                break
            threading.Event().wait(0.01)
        assert runs.active == 0
        assert (
            client.post("/api/runs", json={"request": "after the burst"}).status_code
            == 202
        )


async def test_thirty_listeners_get_identical_events_and_leave_nothing_behind() -> None:
    bus = EventBus()
    bus.open("r")

    async def listen() -> list[int]:
        return [event.seq async for event in bus.subscribe("r")]

    tasks = [asyncio.create_task(listen()) for _ in range(30)]
    await asyncio.sleep(0)
    for kind in ("run_started", "step_started", "run_completed"):
        bus.publish("r", kind)  # type: ignore[arg-type]
    results = await asyncio.gather(*tasks)
    assert all(result == [1, 2, 3] for result in results)
    assert bus._channels["r"].queues == []  # every listener unregistered itself


async def test_a_listener_that_disconnects_midway_is_removed() -> None:
    bus = EventBus()
    bus.open("r")
    seen: list[int] = []

    async def listen() -> None:
        async for event in bus.subscribe("r"):
            seen.append(event.seq)

    task = asyncio.create_task(listen())
    await asyncio.sleep(0)
    bus.publish("r", "run_started")
    await asyncio.sleep(0)
    task.cancel()  # the browser went away
    await asyncio.gather(task, return_exceptions=True)
    assert bus._channels["r"].queues == []
    bus.publish(
        "r", "step_started"
    )  # publishing to a run nobody listens to is harmless
    assert seen == [1]


# ------------------------------------------------------------------ awkward text


@pytest.mark.parametrize("text", AWKWARD.values(), ids=AWKWARD.keys())
def test_awkward_text_survives_the_whole_api_round_trip(
    client: TestClient, text: str
) -> None:
    run_id = start(client, text)
    events = sse_events(client, run_id)
    assert events[0]["data"]["request"] == text  # through the graph, the bus and SSE
    assert events[-1]["type"] == "run_completed"
    store = client.app.state.services.store  # type: ignore[attr-defined]
    wait_until_stored(store, run_id)
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["request"] == text  # and through SQLite
    assert detail["status"] == "completed"
    listed = client.get("/api/runs").json()
    assert listed[0]["run_id"] == run_id
    assert client.get(f"/api/runs/{run_id}/report").status_code == 200


@pytest.mark.parametrize("text", AWKWARD.values(), ids=AWKWARD.keys())
async def test_awkward_text_survives_the_store_and_its_json_columns(
    tmp_path: Path, text: str
) -> None:
    store = RunStore(tmp_path / "s.db")
    store.open()
    await store.create_run("r1", text)
    await store.finish_run("r1", "completed", {"headline": text, "title": text})
    run = await store.get_run("r1")
    assert (
        run is not None
        and run.request == text
        and run.report == {"headline": text, "title": text}
    )
    summary = (await store.list_summaries())[0]
    assert (summary.headline, summary.title) == (
        text,
        text,
    )  # json_extract keeps it intact


def test_a_nul_character_is_handled_without_a_server_error(client: TestClient) -> None:
    response = client.post("/api/runs", json={"request": "before\x00after"})
    assert response.status_code < 500


@pytest.mark.xfail(
    strict=True,
    reason="Finding F2: a lone surrogate (invalid text) in the request makes SQLite raise and the "
    "API answer 500 instead of a clear 422.",
)
def test_invalid_unicode_is_refused_cleanly_not_with_a_server_error(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/runs",
        content=b'{"request": "bad \\ud800 text"}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code in (202, 422)


def test_awkward_text_fits_notion_pages() -> (  # the adapter's own validation and block building
    None
):
    for text in AWKWARD.values():
        params = CreatePageParams(
            title=text[:200] or "t", content=text, action_items=[text[:500]]
        )
        blocks = build_children(params)
        assert blocks and all("object" in block for block in blocks)


# ------------------------------------------------------------------ Drive file names


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("Q3/Q4 plan.md", id="slash"),
        pytest.param("Plan\\notes.md", id="backslash"),
        pytest.param("line one\nline two.md", id="newline"),
    ],
)
@pytest.mark.xfail(
    strict=True,
    reason="Finding F1: a title with a slash or line break is rejected by the file-name check, "
    "so the Drive step fails and the whole run rolls back, instead of cleaning the name.",
)
async def test_a_title_with_forbidden_characters_still_makes_a_file(name: str) -> None:
    mcp = FakeMcp(created("whatever.md", "id-1"))
    result = await DriveAdapter(mcp, "folder").execute(
        "create_text_file", {"name": name}
    )
    sent = mcp.calls[0][2]["name"]
    assert result.url and not any(ch in sent for ch in "/\\\r\n")


@pytest.mark.xfail(
    strict=True,
    reason="Finding F1: a title longer than 200 characters (the server limit) fails the step "
    "instead of being shortened.",
)
async def test_a_very_long_title_is_shortened_not_refused() -> None:
    mcp = FakeMcp(created("x.md", "id-1"))
    await DriveAdapter(mcp, "folder").execute(
        "create_text_file", {"name": "T" * 250 + ".md"}
    )
    assert len(mcp.calls[0][2]["name"]) <= MAX_NAME_CHARS


async def test_forbidden_names_fail_fast_and_never_reach_drive() -> None:
    """Today's behaviour, pinned: a bad name is a clear error before anything is created."""
    mcp = FakeMcp()
    with pytest.raises(FatalToolError, match="Invalid parameters"):
        await DriveAdapter(mcp, "folder").execute(
            "create_text_file", {"name": "a/b.md"}
        )
    assert mcp.calls == []


# ------------------------------------------------------------------ boundaries


def test_a_request_of_exactly_the_limit_is_accepted_and_one_more_is_not(
    client: TestClient,
) -> None:
    assert client.post("/api/runs", json={"request": "x" * 6000}).status_code == 202
    assert client.post("/api/runs", json={"request": "x" * 6001}).status_code == 422


# ------------------------------------------------------------------ what reaches the team chat

HOSTILE = "<!channel> <!here|here> <@U12345> <https://evil.example|free money> & done"


def test_slack_escapes_every_mention_and_link_in_user_text() -> None:
    payload = SlackNotifier(URL).render(
        NotificationMessage(
            title=HOSTILE,
            summary=HOSTILE,
            facts=[{"name": HOSTILE, "value": HOSTILE}],  # type: ignore[list-item]
            links=[{"label": HOSTILE, "url": "https://example.com/a|b"}],  # type: ignore[list-item]
        )
    )
    text = json.dumps(payload)
    # Only the link markup WE build may contain an angle bracket; the user's text must not.
    allowed = "<https://example.com/a%7Cb|"
    assert text.count("<") == text.count(allowed)
    assert "<!channel>" not in text and "<@U12345>" not in text


@pytest.mark.xfail(
    strict=True,
    reason="Finding F3: Teams cards show title and summary as Markdown, so text like "
    "[click](https://evil.example) from the notes or the LLM becomes a real link in the team "
    "message. (Slack escapes this; Teams does not.)",
)
def test_teams_does_not_turn_user_text_into_links() -> None:
    card = TeamsNotifier(URL).render(
        NotificationMessage(
            title="Done", summary="Please [click here](https://evil.example/x)"
        )
    )
    body = card["attachments"][0]["content"]["body"]
    assert not any("](https://evil.example" in block.get("text", "") for block in body)


def test_teams_user_text_cannot_create_a_mention_entity() -> None:
    card = TeamsNotifier(URL).render(
        NotificationMessage(title="<at>Everyone</at>", summary="<at>Everyone</at> hi")
    )
    content = card["attachments"][0]["content"]
    assert "msteams" not in content and "entities" not in json.dumps(card)


# ------------------------------------------------------------------ restarts


def test_a_run_cut_off_by_a_restart_is_settled_listed_and_replayable(
    settings: Settings,
) -> None:
    gate = (
        threading.Event()
    )  # never released: the run is still planning when the server stops
    with TestClient(
        create_app(settings, InMemoryTracer(), fake_runtime(gate=gate))
    ) as c:
        run_id = start(c, "interrupted")
    gate.set()
    with TestClient(create_app(settings, InMemoryTracer(), fake_runtime())) as c:
        listed = c.get("/api/runs").json()
        assert [(r["run_id"], r["status"]) for r in listed] == [(run_id, "failed")]
        assert "restarted" in listed[0]["headline"]
        messages = read_stream(c, run_id)  # a replay must end, not hang
        assert messages[-1]["event"] == "run_completed"
        assert c.get(f"/api/runs/{run_id}/report").status_code == 200
