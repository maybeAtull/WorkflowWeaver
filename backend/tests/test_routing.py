from typing import Any, cast

import pytest

from app.agent.routing import (
    EXECUTE,
    PLAN,
    REPORT,
    ROLLBACK,
    VERIFY,
    decide_after_step,
    final_status_after_rollback,
    route_after_intent,
    route_after_plan,
    route_after_step,
    route_after_verify,
)
from app.agent.state import CompletedAction, RollbackRecord, WorkflowState


def state(status: str) -> WorkflowState:
    return cast(WorkflowState, {"status": status})


@pytest.mark.parametrize(
    ("ok", "is_last", "irreversible", "did_work", "verified", "next_irrev", "expected"),
    [
        # More reversible work to come: just carry on.
        (True, False, False, True, False, False, "running"),
        # The irreversible step is next: read everything back first.
        (True, False, False, True, False, True, "verifying"),
        # ...but only once.
        (True, False, False, True, True, True, "running"),
        # The end of a plan with no notification is also verified.
        (True, True, False, True, False, False, "verifying"),
        (True, True, False, True, True, False, "completed"),
        # The notification finished after a successful check: done.
        (True, True, True, True, True, False, "completed"),
        (False, False, False, True, False, False, "rolling_back"),
        (False, False, False, False, False, False, "rolling_back"),
        # The final notification failing keeps the finished work...
        (False, True, True, True, True, False, "completed_with_warning"),
        # ...but a notification that is the only thing planned has no work to keep.
        (False, True, True, False, False, False, "rolling_back"),
    ],
)
def test_status_after_a_step(
    ok: bool,
    is_last: bool,
    irreversible: bool,
    did_work: bool,
    verified: bool,
    next_irrev: bool,
    expected: str,
) -> None:
    assert (
        decide_after_step(
            ok=ok,
            is_last=is_last,
            irreversible=irreversible,
            did_work=did_work,
            verified=verified,
            next_is_irreversible=next_irrev,
        )
        == expected
    )


def done(step_id: str = "s1") -> CompletedAction:
    return CompletedAction(step_id=step_id, tool="t", action="a", summary="x")


def rb(status: str) -> RollbackRecord:
    return RollbackRecord(
        step_id="s1",
        tool="t",
        action="a",
        status=cast(Any, status),
    )


@pytest.mark.parametrize(
    ("completed", "records", "expected"),
    [
        ([done()], [rb("undone")], "rolled_back"),
        ([done(), done("s2")], [rb("undone"), rb("skipped")], "rolled_back"),
        ([done()], [rb("failed")], "failed"),  # something was left behind
        ([done(), done("s2")], [rb("undone"), rb("failed")], "failed"),
        ([], [], "failed"),  # nothing was done, so nothing to roll back
        ([done()], [rb("skipped")], "failed"),  # nothing actually undone
    ],
)
def test_final_status_after_rollback(
    completed: list[CompletedAction], records: list[RollbackRecord], expected: str
) -> None:
    assert final_status_after_rollback(completed, records) == expected


def test_an_unusable_request_goes_straight_to_the_report() -> None:
    assert route_after_intent(state("failed")) == REPORT
    assert route_after_intent(state("planning")) == PLAN


def test_a_failed_plan_goes_straight_to_the_report() -> None:
    assert route_after_plan(state("failed")) == REPORT
    assert route_after_plan(state("running")) == EXECUTE


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("running", EXECUTE),
        ("verifying", VERIFY),
        ("rolling_back", ROLLBACK),
        ("completed", REPORT),
        ("completed_with_warning", REPORT),
    ],
)
def test_route_after_a_step(status: str, expected: str) -> None:
    assert route_after_step(state(status)) == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("running", EXECUTE),  # the notification is still to come
        ("rolling_back", ROLLBACK),  # a check failed: undo before anything is announced
        ("completed", REPORT),
    ],
)
def test_route_after_verifying(status: str, expected: str) -> None:
    assert route_after_verify(state(status)) == expected
