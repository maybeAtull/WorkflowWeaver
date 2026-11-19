"""Run one workflow end to end from the command line, printing live progress.

Run from the backend/ folder:
  python -m scripts.run_workflow --live [--simulate STEP[:MODE]] [--cleanup] [--request TEXT]

THIS WRITES TO REAL SERVICES: it creates a Notion page and a Drive file and posts one chat
message, which is why it refuses to run without --live. It uses the real LLM and the settings
in .env. The progress lines are the same events the browser will receive over SSE.

  --simulate s2:fatal   break step 2 on purpose to see rollback (s2:transient shows a retry)
  --cleanup             after a successful run, move the created Notion page and Drive file
                        to the trash (the chat message cannot be removed through a webhook)

Never prints keys, tokens or webhook URLs. Exit code 0 only if the run completed.
"""

import argparse
import asyncio
import json
import sys
import uuid
from typing import Any

from app.agent.graph import run_workflow
from app.agent.runtime import agent_runtime
from app.config import get_settings
from app.events import EventBus, RunEvent
from app.observability.langfuse_tracer import LangfuseTracer, create_tracer
from app.tools.base import ToolError
from scripts.smoke_intent import REQUEST  # the synthetic "Project Falcon" request


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one workflow end to end.")
    parser.add_argument(
        "--live", action="store_true", help="required: acknowledges real writes"
    )
    parser.add_argument(
        "--simulate", default=None, help="failure injection: STEP[:MODE]"
    )
    parser.add_argument(
        "--cleanup", action="store_true", help="trash created items afterwards"
    )
    parser.add_argument(
        "--request", default=REQUEST, help="the workflow, in plain English"
    )
    args = parser.parse_args(argv)
    if not args.live:
        parser.error(
            "refusing to run without --live: this creates a real Notion page and Drive file "
            "and posts a chat message."
        )
    return args


def format_event(event: RunEvent) -> str:
    """One plain, ASCII line per event (a Windows console must not choke on it)."""
    d: dict[str, Any] = event.data
    kind = event.type
    if kind == "run_started":
        return "Starting the workflow."
    if kind == "plan_ready":
        steps = [
            f"  {n}. {s.get('description') or str(s.get('tool')) + '.' + str(s.get('action'))}"
            for n, s in enumerate(d.get("steps", []), start=1)
        ]
        return "Plan ready:\n" + "\n".join(steps)
    if kind == "step_started":
        what = d.get("description") or f"{d.get('tool')}.{d.get('action')}"
        return f"[{d.get('position')}] {what}"
    if kind == "step_retry":
        return f"    retry {d.get('attempt')} in {d.get('delay_seconds')}s: {d.get('reason')}"
    if kind == "step_succeeded":
        return f"    done: {d.get('summary')}"
    if kind == "step_failed":
        return f"    FAILED: {d.get('error')}"
    if kind == "verify_started":
        return f"Checking {d.get('total')} created item(s) before announcing anything."
    if kind == "verify_step":
        return f"    {d.get('status')}: {d.get('tool')}.{d.get('action')} - {d.get('detail')}"
    if kind == "rollback_started":
        return f"Rolling back {d.get('to_undo')} item(s)."
    if kind == "rollback_step":
        return f"    {d.get('status')}: {d.get('tool')}.{d.get('action')} {d.get('detail', '')}".rstrip()
    if kind == "run_completed":
        return f"Finished ({d.get('status')}). {d.get('headline')}"
    return f"{kind}: {d}"


async def _print_events(bus: EventBus, run_id: str) -> None:
    async for event in bus.subscribe(run_id):
        print(format_event(event), flush=True)


async def main(args: argparse.Namespace) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    settings = get_settings()
    print(
        f"LIVE run. LLM: {settings.llm_provider}; notifier: {settings.notifier_provider}; "
        f"failure injection: {args.simulate or settings.simulate_failure_step or 'off'}"
    )
    bus = EventBus()
    run_id = uuid.uuid4().hex[:8]
    printer = asyncio.create_task(_print_events(bus, run_id))
    tracer = create_tracer(settings)  # the run's trace goes to Langfuse

    try:
        async with agent_runtime(
            settings, bus, simulate=args.simulate, tracer=tracer
        ) as deps:
            final = await run_workflow(deps, run_id, args.request)
            await printer  # let the last event print before the summary

            report = final["report"] or {}
            print("\n--- report ---")
            print(json.dumps(report, indent=2, ensure_ascii=False))

            if args.cleanup and final["status"] == "completed":
                print("\n--- cleanup (moving created items to the trash) ---")
                for action in reversed(final["completed"]):
                    if action.undo_handle is None:
                        continue
                    try:
                        await deps.registry.undo(action.undo_handle)
                        print(f"trashed: {action.summary}")
                    except ToolError as error:
                        print(f"could not trash {action.summary}: {error}")
        # agent_runtime has flushed the trace by now, so the link is ready to open.
        print(f"\nLangfuse trace: {_trace_link(tracer)}")
    finally:
        tracer.shutdown()

    return 0 if final["status"] == "completed" else 1


def _trace_link(tracer: LangfuseTracer) -> str:
    try:
        return tracer.last_trace_url() or "(not available)"
    except Exception:  # noqa: BLE001 - a missing link must not fail the run
        return "(not available)"


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse_args())))
