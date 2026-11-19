"""The planner drills' own pieces, proven offline (the LLM part is run live)."""

import pytest

from app.agent.nodes.parse_intent import MAX_REQUEST_CHARS
from app.agent.state import PlanStep
from scripts import smoke_plan_drills as drills
from scripts.smoke_plan_drills import big_request, check_plan, hostile_request


def step(tool: str, action: str = "x") -> PlanStep:
    return PlanStep(id=f"s-{tool}", tool=tool, action=action, params={})


def failed(steps: list[PlanStep]) -> list[str]:
    return [c.name for c in check_plan(steps) if not c.ok]


def test_the_big_request_is_close_to_but_under_the_limit() -> None:
    text = big_request()
    assert MAX_REQUEST_CHARS - 200 < len(text) <= MAX_REQUEST_CHARS


def test_the_hostile_request_still_contains_real_work_and_the_attack_text() -> None:
    text = hostile_request()
    assert "Notion" in text and "IGNORE ALL PREVIOUS INSTRUCTIONS" in text
    assert "<!channel>" in text and "evil.example" in text


def test_a_normal_plan_passes_every_check() -> None:
    assert failed([step("notion"), step("drive"), step("notifier")]) == []


def test_an_unknown_tool_is_caught() -> None:
    assert "only the real tools are used" in failed([step("shell"), step("notifier")])


def test_the_notification_must_be_last_and_only_once() -> None:
    assert "the notification is the last step" in failed(
        [step("notifier"), step("notion")]
    )
    assert "the notification is the last step" in failed(
        [step("notifier"), step("notifier")]
    )
    assert "the notification is the last step" in failed([step("notion")])


def test_too_many_steps_or_none_is_caught() -> None:
    assert "within the step limit" in failed([])
    many = [step("notion")] * 11 + [step("notifier")]
    assert "within the step limit" in failed(many)


def test_it_refuses_to_run_without_live(capsys: pytest.CaptureFixture[str]) -> None:
    import runpy
    import sys

    sys.argv = ["smoke_plan_drills"]
    with pytest.raises(SystemExit) as stop:
        runpy.run_module("scripts.smoke_plan_drills", run_name="__main__")
    assert stop.value.code == 2
    assert "refusing to run without --live" in capsys.readouterr().err
    assert drills.REAL_TOOLS == {"notion", "drive", "notifier"}
