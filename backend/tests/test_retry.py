import pytest

from app.agent.retry import MAX_RETRIES, backoff_delay


def test_delay_doubles_and_is_capped_without_jitter() -> None:
    delays = [backoff_delay(n, rng=lambda: 0.0) for n in (1, 2, 3, 4, 5)]
    assert delays == [1.0, 2.0, 4.0, 8.0, 8.0]


def test_jitter_only_adds_up_to_a_quarter() -> None:
    assert backoff_delay(2, rng=lambda: 1.0) == pytest.approx(2.5)
    assert backoff_delay(2, rng=lambda: 0.5) == pytest.approx(2.25)


def test_real_random_source_stays_within_bounds() -> None:
    for _ in range(50):
        assert 4.0 <= backoff_delay(3) <= 5.0


def test_retry_numbers_start_at_one() -> None:
    with pytest.raises(ValueError):
        backoff_delay(0)


def test_the_retry_limit_is_three() -> None:
    assert MAX_RETRIES == 3
