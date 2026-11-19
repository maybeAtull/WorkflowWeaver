import json
from datetime import datetime, timezone
from typing import Any

import pytest

from app.agent.report import build_report
from app.agent.state import (
    CompletedAction,
    Intent,
    PlanStep,
    RollbackRecord,
    StepRecord,
    VerifyRecord,
    WorkflowState,
    new_state,
)
from app.tools.base import UndoHandle

STARTED = "2026-01-01T12:00:00+00:00"
NOW = datetime(2026, 1, 1, 12, 0, 30, tzinfo=timezone.utc)  # noqa: UP017 (Python 3.10)


def record(step_id: str, status: str, **extra: Any) -> StepRecord:
    return StepRecord(
        step=PlanStep(
            id=step_id, tool="notion", action="create_page", description=step_id
        ),
        status=status,  # type: ignore[arg-type]
        **extra,
    )


def done(step_id: str, url: str | None = "https://example.test/x") -> CompletedAction:
    return CompletedAction(
        step_id=step_id,
        tool="notion",
        action="create_page",
        summary=f"Created {step_id}",
        url=url,
        undo_handle=UndoHandle(
            tool="notion", action="create_page", data={"id": step_id}
        ),
    )


def check(step_id: str, status: str) -> VerifyRecord:
    return VerifyRecord(
        step_id=step_id, tool="notion", action="create_page", status=status  # type: ignore[arg-type]
    )


def make_state(status: str, **fields: Any) -> WorkflowState:
    state = new_state("run-1", "request")
    state["started_at"] = STARTED
    state["status"] = status  # type: ignore[typeddict-item]
    state.update(fields)  # type: ignore[typeddict-item]
    return state


def intent() -> Intent:
    return Intent(
        title="Weekly sync",
        summary="A short fictional sync.",
        kind="meeting_notes",
        action_items=[],
        decisions=[],
        requested_tools=["notion"],
        clarification_needed="",
    )


# ------------------------------------------------------------------ headlines


def test_success_with_every_item_confirmed() -> None:
    state = make_state(
        "completed",
        step_records=[record("s1", "succeeded"), record("s2", "succeeded")],
        completed=[done("s1"), done("s2")],
        checks=[check("s1", "verified"), check("s2", "verified")],
    )
    report = build_report(state, NOW)
    assert report["headline"] == (
        "Done: all 2 steps finished and the created items were confirmed."
    )


def test_success_mentions_items_that_could_not_be_confirmed() -> None:
    state = make_state(
        "completed",
        step_records=[record("s1", "succeeded")],
        completed=[done("s1")],
        checks=[check("s1", "unconfirmed")],
    )
    assert build_report(state, NOW)["headline"] == (
        "Done: all 1 step finished, but 1 item could not be confirmed."
    )


def test_success_without_any_checks_does_not_claim_confirmation() -> None:
    state = make_state("completed", step_records=[record("s1", "succeeded")])
    assert build_report(state, NOW)["headline"] == "Done: all 1 step finished."


def test_a_failed_message_keeps_the_work_and_says_so() -> None:
    state = make_state(
        "completed_with_warning",
        step_records=[record("s1", "succeeded"), record("s2", "failed")],
        completed=[done("s1")],
        error="webhook gone",
    )
    report = build_report(state, NOW)
    assert report["headline"] == (
        "Your work was saved, but the team message could not be sent."
    )
    assert [c["step_id"] for c in report["created"]] == ["s1"]


def test_a_rollback_says_everything_was_undone_and_why() -> None:
    state = make_state(
        "rolled_back",
        step_records=[record("s1", "succeeded"), record("s2", "failed")],
        completed=[done("s1")],
        rollbacks=[
            RollbackRecord(
                step_id="s1", tool="notion", action="create_page", status="undone"
            )
        ],
        error="no access to Drive",
    )
    report = build_report(state, NOW)
    assert report["headline"] == (
        "Stopped. Everything that had been created was undone. Reason: no access to Drive"
    )
    assert report["created"] == []  # those items are in the trash now
    assert report["needs_cleanup"] == []


def test_a_failure_before_anything_was_created_is_plain() -> None:
    state = make_state("failed", error="What should the page say?")
    assert build_report(state, NOW)["headline"] == (
        "The workflow could not run: What should the page say?"
    )


def test_a_failed_undo_is_listed_for_manual_cleanup_with_its_link() -> None:
    state = make_state(
        "failed",
        step_records=[record("s1", "succeeded"), record("s2", "failed")],
        completed=[done("s1", url="https://notion.example/page-1")],
        rollbacks=[
            RollbackRecord(
                step_id="s1",
                tool="notion",
                action="create_page",
                status="failed",
                detail="no permission",
            )
        ],
        error="drive failed",
    )
    report = build_report(state, NOW)
    assert report["headline"].startswith(
        "Stopped, but 1 item could not be removed and need manual cleanup."
    )
    assert report["needs_cleanup"] == [
        {
            "step_id": "s1",
            "label": "Created s1",
            "url": "https://notion.example/page-1",
            "reason": "no permission",
        }
    ]


def test_a_missing_error_still_gives_a_readable_headline() -> None:
    assert "unknown reason" in build_report(make_state("failed"), NOW)["headline"]


# ------------------------------------------------------------------ content


def test_created_links_appear_only_for_items_with_a_url() -> None:
    state = make_state(
        "completed",
        step_records=[record("s1", "succeeded"), record("s2", "succeeded")],
        completed=[done("s1"), done("s2", url=None)],
    )
    assert [c["step_id"] for c in build_report(state, NOW)["created"]] == ["s1"]


def test_counts_and_step_details() -> None:
    state = make_state(
        "rolled_back",
        step_records=[
            record("s1", "succeeded", attempts=3, summary="ok", url="https://e.test/1"),
            record("s2", "failed", attempts=1, error="boom"),
            record("s3", "skipped"),
        ],
    )
    report = build_report(state, NOW)
    assert report["counts"] == {
        "total": 3,
        "succeeded": 1,
        "failed": 1,
        "skipped": 1,
        "pending": 0,
    }
    first = report["steps"][0]
    assert (first["id"], first["attempts"], first["url"]) == (
        "s1",
        3,
        "https://e.test/1",
    )
    assert report["steps"][1]["error"] == "boom"


def test_timing_is_derived_from_the_start_time() -> None:
    report = build_report(make_state("completed"), NOW)
    assert report["started_at"] == STARTED
    assert report["finished_at"] == NOW.isoformat()
    assert report["duration_seconds"] == 30.0


def test_a_malformed_start_time_does_not_break_the_report() -> None:
    report = build_report(make_state("completed", started_at="not a date"), NOW)
    assert report["duration_seconds"] == 0.0


def test_the_title_and_summary_come_from_the_intent_when_there_is_one() -> None:
    with_intent = build_report(make_state("completed", intent=intent()), NOW)
    assert (with_intent["title"], with_intent["summary"]) == (
        "Weekly sync",
        "A short fictional sync.",
    )
    without = build_report(make_state("failed", error="x"), NOW)
    assert without["title"] is None and without["summary"] is None


def test_checks_and_retries_are_included() -> None:
    state = make_state("completed", checks=[check("s1", "verified")], retries=2)
    report = build_report(state, NOW)
    assert report["checks"][0]["status"] == "verified" and report["retries"] == 2


@pytest.mark.parametrize(
    "status", ["completed", "completed_with_warning", "rolled_back", "failed"]
)
def test_every_report_is_plain_json(status: str) -> None:
    state = make_state(
        status,
        intent=intent(),
        step_records=[record("s1", "succeeded")],
        completed=[done("s1")],
        checks=[check("s1", "verified")],
        rollbacks=[
            RollbackRecord(
                step_id="s1", tool="notion", action="create_page", status="undone"
            )
        ],
    )
    report = build_report(state, NOW)
    assert json.loads(json.dumps(report)) == report
    assert report["schema_version"] == 1
