from datetime import datetime

import pytest

from app.events import RunEvent
from scripts.run_workflow import format_event, parse_args
from scripts.smoke_intent import REQUEST


def test_it_refuses_to_run_without_the_live_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as info:
        parse_args([])
    assert info.value.code == 2
    assert "--live" in capsys.readouterr().err


def test_the_defaults_with_live() -> None:
    args = parse_args(["--live"])
    assert args.request == REQUEST
    assert args.simulate is None and args.cleanup is False


def test_the_options_are_read() -> None:
    args = parse_args(
        ["--live", "--simulate", "s2:fatal", "--cleanup", "--request", "hi"]
    )
    assert (args.simulate, args.cleanup, args.request) == ("s2:fatal", True, "hi")


def event(kind: str, **data: object) -> RunEvent:
    return RunEvent(run_id="r", seq=1, type=kind, data=data)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kind", "data", "expected"),
    [
        ("run_started", {}, "Starting"),
        (
            "plan_ready",
            {
                "steps": [
                    {
                        "tool": "notion",
                        "action": "create_page",
                        "description": "Make a page.",
                    }
                ]
            },
            "1. Make a page.",
        ),
        (
            "step_started",
            {"position": 2, "description": "Save a copy."},
            "[2] Save a copy.",
        ),
        (
            "step_started",
            {"position": 1, "tool": "drive", "action": "create_text_file"},
            "drive.create_text_file",
        ),
        (
            "step_retry",
            {"attempt": 2, "delay_seconds": 1.0, "reason": "busy"},
            "retry 2 in 1.0s: busy",
        ),
        ("step_succeeded", {"summary": "Created page"}, "done: Created page"),
        ("step_failed", {"error": "boom"}, "FAILED: boom"),
        ("verify_started", {"total": 2}, "Checking 2"),
        (
            "verify_step",
            {
                "status": "verified",
                "tool": "notion",
                "action": "create_page",
                "detail": "ok",
            },
            "verified: notion.create_page - ok",
        ),
        ("rollback_started", {"to_undo": 1}, "Rolling back 1"),
        (
            "rollback_step",
            {
                "status": "undone",
                "tool": "notion",
                "action": "create_page",
                "detail": "undone: x",
            },
            "undone: notion.create_page",
        ),
        (
            "run_completed",
            {"status": "completed", "headline": "Done."},
            "Finished (completed). Done.",
        ),
    ],
)
def test_events_become_plain_ascii_lines(kind: str, data: dict, expected: str) -> None:
    line = format_event(event(kind, **data))
    assert expected in line
    assert line.isascii()  # a Windows console must not choke on it


def test_run_events_carry_a_timezone_aware_start_time() -> None:
    assert datetime.fromisoformat(event("run_started").timestamp).tzinfo is not None
