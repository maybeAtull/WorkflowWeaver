"""Smoke test for the notifier (Slack or Teams, per NOTIFIER_PROVIDER).

Run from the backend/ folder:
  python -m scripts.smoke_notifier_adapter                 POSTS one synthetic message to the
                                                           configured chat (cannot be undone
                                                           through the webhook)
  python -m scripts.smoke_notifier_adapter --teams-preview prints the exact JSON the Teams
                                                           adapter would send. No network and no
                                                           settings needed. Paste it into the
                                                           Adaptive Cards designer or a test Teams
                                                           workflow to check how it renders.

Never prints the webhook URL. Exits non-zero if the send fails.
"""

import asyncio
import json
import sys

from pydantic import ValidationError

from app.config import get_settings
from app.tools.base import ToolError
from app.tools.notifier import Fact, Link, NotificationMessage, get_notifier
from app.tools.teams import TeamsNotifier

# Synthetic content only (never real data).
SAMPLE = NotificationMessage(
    title="WorkflowWeaver notifier smoke test",
    summary="Synthetic message. If you can read this, the webhook works.",
    status="success",
    facts=[
        Fact(name="Notion page", value="(synthetic)"),
        Fact(name="Drive file", value="(synthetic)"),
    ],
    links=[Link(label="Example link", url="https://example.com")],
)


def report(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    return ok


def preview_teams() -> int:
    # The card contains emoji; make sure a Windows console does not choke on them.
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    payload = TeamsNotifier("https://example.invalid/preview").render(SAMPLE)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


async def send() -> int:
    try:
        settings = get_settings()
    except ValidationError as exc:
        # Field names and messages only; never the offending input values.
        problems = [
            f"{'.'.join(str(p) for p in e['loc']) or 'settings'}: {e['msg']}"
            for e in exc.errors()
        ]
        report("config loads", False, "; ".join(problems))
        return 1
    report("config loads", True, f"notifier provider = {settings.notifier_provider}")

    notifier = get_notifier(settings)
    try:
        result = await notifier.execute("send", SAMPLE.model_dump())
    except ToolError as exc:
        return 0 if report("send", False, f"{type(exc).__name__}: {exc}") else 1
    ok = report(
        "send",
        result.undo_handle is None,
        f"provider={result.output['provider']}, HTTP {result.output['status_code']}, "
        f"delivery confirmed by the webhook={result.output['delivery_confirmed']}",
    )
    return 0 if ok else 1


def main() -> int:
    if "--teams-preview" in sys.argv[1:]:
        return preview_teams()
    return 0 if asyncio.run(send()) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
