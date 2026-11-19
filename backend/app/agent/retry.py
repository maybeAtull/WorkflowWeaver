"""Retry timing: exponential backoff with jitter.

Waits grow 1s, 2s, 4s (capped at 8s). The jitter spreads retries out, so many runs that hit
the same rate limit do not all retry at the same instant.
"""

import random
from collections.abc import Callable

# Retries AFTER the first attempt, so a step is tried at most 1 + MAX_RETRIES times.
MAX_RETRIES = 3


def backoff_delay(
    retry_number: int,
    *,
    base: float = 1.0,
    cap: float = 8.0,
    jitter: float = 0.25,
    rng: Callable[[], float] = random.random,
) -> float:
    """Seconds to wait before retry number `retry_number` (1 for the first retry)."""
    if retry_number < 1:
        raise ValueError("retry_number starts at 1")
    delay = min(cap, base * 2 ** (retry_number - 1))
    # Jitter only adds time (up to `jitter` of the delay) so the minimum wait is guaranteed.
    return delay * (1 + jitter * rng())
