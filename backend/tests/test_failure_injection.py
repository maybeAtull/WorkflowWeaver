import pytest

from app.agent.failure_injection import Injection, parse_injection
from app.tools.base import FatalToolError, TransientToolError


@pytest.mark.parametrize("value", ["", "   ", ":fatal", ":"])
def test_empty_values_disable_injection(value: str) -> None:
    assert parse_injection(value) is None


def test_a_bare_step_means_fatal() -> None:
    assert parse_injection("s2") == Injection("s2", "fatal")


def test_modes_are_parsed_case_and_space_insensitively() -> None:
    assert parse_injection(" s2 : Transient ") == Injection("s2", "transient")
    assert parse_injection("2:fatal") == Injection("2", "fatal")


def test_an_unknown_mode_is_ignored_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert parse_injection("s2:explode") is None
    assert "unknown mode" in caplog.text


def test_it_applies_by_step_id_or_one_based_position() -> None:
    injection = Injection("s2", "fatal")
    assert injection.applies_to("s2", 5)
    assert not injection.applies_to("s3", 5)
    by_position = Injection("2", "fatal")
    assert by_position.applies_to("anything", 2)
    assert not by_position.applies_to("anything", 3)


def test_other_steps_are_untouched() -> None:
    Injection("s2", "fatal").check("s1", 1, attempt=1)  # no error


def test_fatal_fails_every_attempt() -> None:
    injection = Injection("s2", "fatal")
    for attempt in (1, 2, 3, 4):
        with pytest.raises(FatalToolError):
            injection.check("s2", 2, attempt)


def test_transient_fails_twice_then_lets_the_call_through() -> None:
    injection = Injection("s2", "transient")
    for attempt in (1, 2):
        with pytest.raises(TransientToolError) as info:
            injection.check("s2", 2, attempt)
        assert info.value.outcome_unknown is False
    injection.check("s2", 2, attempt=3)  # no error
