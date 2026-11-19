"""Failure injection: break a chosen step on purpose so retry and rollback can be demoed.

Controlled by the SIMULATE_FAILURE_STEP setting, format `STEP` or `STEP:MODE`:
  - STEP is a step id ("s2", the documented form) or a 1-based position ("2").
  - MODE is "fatal" (default: every attempt fails, so the run rolls back) or "transient"
    (the first two attempts fail with a retryable error, then the real call goes through).
Empty or unreadable values disable injection; a typo must never crash a real run.

The fake error is raised BEFORE the real call, so a failed attempt never creates anything.
"""

import logging
from dataclasses import dataclass
from typing import Literal

from app.tools.base import FatalToolError, TransientToolError

logger = logging.getLogger(__name__)

Mode = Literal["fatal", "transient"]
_MODES: tuple[Mode, ...] = ("fatal", "transient")
# With MAX_RETRIES = 3 the third attempt succeeds, which makes a short, clear retry demo.
TRANSIENT_FAILURES = 2


@dataclass(frozen=True)
class Injection:
    step_ref: str
    mode: Mode

    def applies_to(self, step_id: str, position: int) -> bool:
        return self.step_ref in (step_id, str(position))

    def check(self, step_id: str, position: int, attempt: int) -> None:
        """Raise the injected error if this step and attempt are targeted."""
        if not self.applies_to(step_id, position):
            return
        if self.mode == "fatal":
            raise FatalToolError("Injected failure (SIMULATE_FAILURE_STEP)")
        if attempt <= TRANSIENT_FAILURES:
            # outcome_unknown=False so the retry demo also works for actions that are not
            # repeat-safe; the "unknown outcome" branch is covered by unit tests instead.
            raise TransientToolError(
                "Injected temporary failure (SIMULATE_FAILURE_STEP)",
                outcome_unknown=False,
            )


def parse_injection(value: str) -> Injection | None:
    ref, _, mode = value.strip().partition(":")
    ref, mode = ref.strip(), mode.strip().lower() or "fatal"
    if not ref:
        return None
    if mode not in _MODES:
        logger.warning(
            "Ignoring SIMULATE_FAILURE_STEP: unknown mode %r (use fatal or transient)",
            mode,
        )
        return None
    return Injection(step_ref=ref, mode=mode)  # type: ignore[arg-type]
