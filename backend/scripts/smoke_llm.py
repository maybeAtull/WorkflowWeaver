"""Smoke test for the configured LLM provider.

Run from the backend/ folder:  python -m scripts.smoke_llm

Steps: (1) config loads, (2) a basic call works, (3) the model returns a valid structured plan.
Never prints secret values. Exits non-zero if any step fails, so it also works as a setup check.
"""

import re
import sys
import time
from typing import Literal

from pydantic import BaseModel, ValidationError

from app.config import ENV_FILE, Settings, get_settings
from app.llm import get_llm

# Synthetic meeting note: never use real data (assessment requirement).
SAMPLE_NOTES = """Weekly sync, Project Falcon (fictional).
Attendees: Alex, Sam, Priya.
- Alex will finalise the launch checklist by Friday.
- Sam will draft the customer FAQ and share it with Priya for review.
- Priya will book the demo room for next Tuesday.
Decision: launch date stays on the 14th."""


class Param(BaseModel):
    name: str
    value: str


class PlanStep(BaseModel):
    tool: Literal["notion", "drive", "notifier"]
    action: str
    params: list[Param]


class Plan(BaseModel):
    steps: list[PlanStep]


def report(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    return ok


def explain_error(exc: Exception, secret: str) -> str:
    """Turn a provider error into a plain-language hint, with the API key redacted."""
    message = str(exc).replace(secret, "***") if secret else str(exc)
    lowered = message.lower()
    # Order matters: a 503 or 429 message can contain other digits, so check those first.
    if re.search(r"\b503\b", lowered) or "unavailable" in lowered:
        hint = "provider is temporarily overloaded (not our code); retry later"
    elif re.search(r"\b429\b", lowered) or "resource_exhausted" in lowered:
        if "perminute" in lowered or "per minute" in lowered:
            hint = "per-minute rate limit hit; wait about a minute and retry"
        elif "perday" in lowered or "per day" in lowered:
            hint = (
                "daily quota used up; wait for the reset or use another model/provider"
            )
        else:
            hint = "rate limit or quota exceeded; check your limits in the provider console"
    elif re.search(r"\b404\b", lowered) or "not_found" in lowered:
        hint = "model not available; set LLM_MODEL to a current model from the provider's list"
    elif re.search(r"\b(400|401|403)\b", lowered) or "api key" in lowered:
        hint = (
            "key rejected; check LLM_API_KEY, LLM_PROVIDER and that the API is enabled"
        )
    else:
        hint = "unexpected error"
    # Full detail on purpose: the quota type is often at the end of the message.
    return f"{type(exc).__name__} ({hint}): {message[:2000]}"


def check_config() -> Settings | None:
    try:
        settings = get_settings()
    except ValidationError as exc:
        # Print only field names and messages, never the offending input values.
        problems = [
            f"{'.'.join(str(p) for p in e['loc']) or 'settings'}: {e['msg']}"
            for e in exc.errors()
        ]
        report("config loads", False, "; ".join(problems))
        return None
    credentials = settings.google_credentials_path
    if not credentials.is_absolute():
        credentials = (
            ENV_FILE.parent / credentials
        )  # relative paths are repo-root relative
    report(
        "config loads",
        True,
        f"llm_provider={settings.llm_provider}, model={settings.llm_model_name}, "
        f"notifier={settings.notifier_provider}, credentials_file_exists={credentials.is_file()}",
    )
    return settings


def check_basic_call(settings: Settings) -> bool:
    secret = settings.llm_api_key.get_secret_value()
    try:
        started = time.perf_counter()
        response = get_llm(settings).invoke("Reply with the single word OK.")
        elapsed = time.perf_counter() - started
    # Any provider failure is a smoke-test failure, so catching broadly is intended here.
    except Exception as exc:  # noqa: BLE001
        return report("basic call", False, explain_error(exc, secret))
    usage = getattr(response, "usage_metadata", None) or {}
    # .text flattens the provider's content blocks into plain text.
    text = str(response.text).strip()[:40]
    return report(
        "basic call", True, f"reply={text!r}, {elapsed:.1f}s, tokens={usage or 'n/a'}"
    )


def check_structured_plan(settings: Settings) -> bool:
    secret = settings.llm_api_key.get_secret_value()
    prompt = (
        "Turn these meeting notes into an ordered workflow plan. Use tool 'notion' to create "
        "pages and tasks, 'drive' to store a notes document, and 'notifier' to post a summary "
        "to the team chat. The notifier step must come last.\n\n" + SAMPLE_NOTES
    )
    try:
        started = time.perf_counter()
        plan = get_llm(settings).with_structured_output(Plan).invoke(prompt)
        elapsed = time.perf_counter() - started
    except Exception as exc:  # noqa: BLE001
        return report("structured plan", False, explain_error(exc, secret))
    if not isinstance(plan, Plan) or not plan.steps:
        return report(
            "structured plan", False, f"expected a non-empty Plan, got {type(plan)}"
        )
    tools = [step.tool for step in plan.steps]
    report("structured plan", True, f"{len(plan.steps)} steps {tools}, {elapsed:.1f}s")
    if tools[-1] != "notifier":
        print(
            "[WARN] notifier is not the last step; prompt tuning needed (not a wiring failure)"
        )
    return True


def main() -> int:
    settings = check_config()
    if settings is None:
        return 1
    results = [check_basic_call(settings), check_structured_plan(settings)]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
