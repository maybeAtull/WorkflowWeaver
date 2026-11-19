"""The live API check's parsing and assertions, proven offline on sample data."""

import json
import socket
from typing import Any

import pytest

from scripts import smoke_api
from scripts.smoke_api import (
    PLANS,
    Plan,
    check_api_agreement,
    check_replay,
    check_report,
    check_stream,
    decode,
    free_port,
    parse_sse,
    show,
)

SUCCESS = Plan("t", None, "completed")
ROLLBACK = PLANS[1]
RETRY = PLANS[2]


def event(seq: int, type_: str, **data: Any) -> dict[str, Any]:
    return {"run_id": "r", "seq": seq, "type": type_, "data": data}


def to_messages(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {"event": e["type"], "id": str(e["seq"]), "data": json.dumps(e)} for e in events
    ]


def success_events() -> list[dict[str, Any]]:
    return [
        event(1, "run_started"),
        event(2, "plan_ready"),
        event(3, "step_started", step_id="s1", tool="notion"),
        event(4, "step_succeeded", step_id="s1"),
        event(5, "step_started", step_id="s2", tool="notifier"),
        event(6, "step_succeeded", step_id="s2"),
        event(7, "run_completed", status="completed"),
    ]


def failed(results: list[Any]) -> list[str]:
    return [name for name, ok, _ in results if not ok]


# ------------------------------------------------------------------ SSE parsing


def test_messages_are_grouped_and_keepalives_ignored() -> None:
    lines = [
        ": ping",
        "event: run_started",
        "id: 1",
        'data: {"a": 1}',
        "",
        "event: run_completed",
        "id: 2",
        "data: {}",
        "",
    ]
    assert parse_sse(lines) == [
        {"event": "run_started", "id": "1", "data": '{"a": 1}'},
        {"event": "run_completed", "id": "2", "data": "{}"},
    ]


def test_a_message_without_a_final_blank_line_is_kept() -> None:
    assert parse_sse(["event: x", "id: 1", "data: {}"]) == [
        {"event": "x", "id": "1", "data": "{}"}
    ]


def test_events_are_decoded_from_their_data() -> None:
    assert decode(to_messages(success_events()))[0]["type"] == "run_started"


# ------------------------------------------------------------------ the stream checks


def test_a_correct_successful_stream_passes() -> None:
    events = success_events()
    assert failed(check_stream(to_messages(events), events, SUCCESS)) == []


def test_a_stream_with_a_gap_or_no_ending_fails() -> None:
    events = success_events()
    gap = to_messages(events)
    gap[3]["id"] = "9"
    assert "stream is ordered and complete" in failed(
        check_stream(gap, events, SUCCESS)
    )
    unfinished = events[:-1]
    assert "stream is ordered and complete" in failed(
        check_stream(to_messages(unfinished), unfinished, SUCCESS)
    )


def test_the_wrong_final_status_fails() -> None:
    events = success_events()
    events[-1] = event(7, "run_completed", status="failed")
    assert "final status is completed" in failed(
        check_stream(to_messages(events), events, SUCCESS)
    )


def test_a_rollback_must_have_no_chat_message_and_the_rollback_events() -> None:
    events = [
        event(1, "run_started"),
        event(2, "plan_ready"),
        event(3, "step_started", step_id="s1", tool="notion"),
        event(4, "step_succeeded", step_id="s1"),
        event(5, "step_started", step_id="s2", tool="drive"),
        event(6, "step_failed", step_id="s2"),
        event(7, "rollback_started"),
        event(8, "rollback_step"),
        event(9, "run_completed", status="rolled_back"),
    ]
    assert failed(check_stream(to_messages(events), events, ROLLBACK)) == []
    # The same run, but a chat message went out: that is exactly what must never happen.
    leaked = (
        events[:6]
        + [
            event(7, "step_started", step_id="s3", tool="notifier"),
            event(8, "step_succeeded", step_id="s3"),
        ]
        + [
            event(9, "rollback_started"),
            event(10, "rollback_step"),
            event(11, "run_completed", status="rolled_back"),
        ]
    )
    assert "NO chat message sent" in failed(
        check_stream(to_messages(leaked), leaked, ROLLBACK)
    )


def test_a_retry_run_must_show_a_retry_event() -> None:
    events = success_events()
    assert "saw step_retry" in failed(check_stream(to_messages(events), events, RETRY))
    events.insert(4, event(0, "step_retry"))
    for n, e in enumerate(events, start=1):
        e["seq"] = n
    assert failed(check_stream(to_messages(events), events, RETRY)) == []


# ------------------------------------------------------------------ report and API checks


def test_a_successful_report_needs_links_and_a_rolled_back_one_must_have_none() -> None:
    good = {
        "status": "completed",
        "headline": "Done",
        "created": [
            {"url": "https://notion.example/p"},
            {"url": "https://drive.example/f"},
        ],
    }
    assert failed(check_report(good, SUCCESS)) == []
    assert failed(check_report({**good, "created": good["created"][:1]}, SUCCESS)) == [
        "created items match the outcome"
    ]
    rolled = {"status": "rolled_back", "headline": "Undone", "created": []}
    assert failed(check_report(rolled, ROLLBACK)) == []
    claimed = {**rolled, "created": good["created"]}
    assert failed(check_report(claimed, ROLLBACK)) == [
        "created items match the outcome"
    ]
    assert failed(check_report(None, SUCCESS)) == ["report exists"]


def test_the_api_views_must_agree() -> None:
    report = {"status": "completed"}
    detail = {
        "status": "completed",
        "trace_url": "https://lf.example/t",
        "report": report,
    }
    listed = [{"run_id": "r1", "status": "completed"}]
    assert failed(check_api_agreement("r1", detail, listed, report, SUCCESS)) == []
    assert failed(check_api_agreement("r1", detail, [], report, SUCCESS)) == [
        "run is in the history list"
    ]
    assert failed(check_api_agreement("r1", detail, listed, {"x": 1}, SUCCESS)) == [
        "report download equals the stored report"
    ]
    no_trace = {**detail, "trace_url": None}
    assert failed(check_api_agreement("r1", no_trace, listed, report, SUCCESS)) == [
        "trace link is present"
    ]


def test_replay_and_resume_are_checked_against_the_live_stream() -> None:
    live = to_messages(success_events())
    assert failed(check_replay(live, live, live[-2:])) == []
    assert len(failed(check_replay(live, live[:-1], live))) == 2


# ------------------------------------------------------------------ plumbing


def test_show_prints_each_result_and_reports_overall_success(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert show([("a", True, ""), ("b", False, "why")]) is False
    out = capsys.readouterr().out
    assert "[PASS] a" in out and "[FAIL] b: why" in out


def test_the_script_refuses_to_run_without_live(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as stop:
        smoke_api.main([])
    assert stop.value.code == 2
    assert "refusing to run without --live" in capsys.readouterr().err


def test_a_free_port_can_really_be_bound() -> None:
    port = free_port()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))


def test_the_plan_matches_what_the_proposal_promised() -> None:
    assert [(p.template_id, p.simulate, p.status) for p in PLANS] == [
        ("meeting_to_tasks", None, "completed"),
        ("sprint_planning", "s2:fatal", "rolled_back"),
        ("incident_response", "s2:transient", "completed"),
    ]
    assert [p.announces for p in PLANS] == [True, False, True]
