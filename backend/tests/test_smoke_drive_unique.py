"""The live Drive check refuses to run without --live (it writes real files)."""

import pytest

from scripts import smoke_drive_unique
from scripts.smoke_drive_unique import RUN_A, RUN_B, make_step, synthetic_title


def test_it_refuses_to_run_without_live(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stop:
        smoke_drive_unique.main([])
    assert stop.value.code == 2
    assert "refusing to run without --live" in capsys.readouterr().err


def test_the_two_runs_have_different_tokens_and_the_title_is_synthetic() -> None:
    from app.agent.nodes.execute_step import run_token

    assert run_token(RUN_A) != run_token(RUN_B)
    assert "WorkflowWeaver" in synthetic_title()
    step = make_step("T")
    assert step.params["name"] == "T.md" and step.tool == "drive"
