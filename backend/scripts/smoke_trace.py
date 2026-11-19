"""Live check of the Langfuse trace: a synthetic workflow through the REAL graph, read back.

Run from the backend/ folder:  python -m scripts.smoke_trace

What is real: the agent graph, the intent parser and planner, the LLM client with its token-usage
callback, the Notion, Drive and Slack/Teams adapters, the verify step, the tracing layer and
Langfuse itself. What is fake: the chat model (a LangChain fake that returns canned answers with
token usage: no LLM tokens are spent), the MCP servers (canned replies) and the chat webhook (a
mock HTTP transport). So nothing is created in Notion or Drive and nothing is posted anywhere.
One Drive create fails once and is retried, so the trace shows an ERROR span and then a success.

The only data sent out is a synthetic trace to Langfuse. The script flushes it, reads it back
through Langfuse's API and asserts: one root, the parent-child structure designed in Task 3.1, the
retry, the generations with model and token usage, and the trace attributes. It prints the trace
link and never prints a key. Exits non-zero if anything is missing.
"""

import asyncio
import sys
import time
import uuid
from typing import Any

import httpx
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.output_parsers import PydanticOutputParser

from app.agent.graph import run_workflow
from app.agent.llm_client import LlmClient
from app.agent.planner import PlannedParam, PlannedStep, PlannedWorkflow
from app.agent.prompts import PROMPT_VERSION
from app.agent.runtime import build_agent_deps
from app.agent.state import ActionItem, Intent
from app.config import Settings, get_settings
from app.events import EventBus
from app.observability.langfuse_tracer import LangfuseTracer, create_tracer
from app.observability.tracing import Tracer
from app.tools.base import TransientToolError

# Ingestion is asynchronous, so the trace can take a while to become readable.
VERIFY_TIMEOUT_SECONDS = 90
VERIFY_POLL_SECONDS = 4
MODEL_NAME = "synthetic-model"
SYNTHETIC_PROVIDER = "synthetic"
REQUEST = "Synthetic request: put the notes in Notion and Drive and tell the team."
DRIVE_ID = "smoke-file-1"
PAGE_JSON = (
    '{"object": "page", "id": "smoke-page-1", "url": "https://example.test/page", '
    '"in_trash": false, "properties": {"title": {"type": "title", '
    '"title": [{"plain_text": "Synthetic"}]}}}'
)

# (child, parent): the tree designed in Task 3.1. Each pair must appear in the trace.
EXPECTED_EDGES = {
    ("parse_intent", "workflow-run"),
    ("llm:parse_intent", "parse_intent"),
    ("plan_steps", "workflow-run"),
    ("llm:plan", "plan_steps"),
    ("execute_step", "workflow-run"),
    ("tool:notion.create_page", "execute_step"),
    ("tool:drive.create_text_file", "execute_step"),
    ("tool:notifier.send", "execute_step"),
    ("verify_work", "workflow-run"),
    ("verify:notion.create_page", "verify_work"),
    ("verify:drive.create_text_file", "verify_work"),
    ("write_report", "workflow-run"),
}


def report(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    return ok


# --------------------------------------------------------------------------- the fakes


class FakeMcp:
    """Canned MCP replies. The first Drive create fails once (a retryable error)."""

    def __init__(self) -> None:
        self.drive_creates = 0

    async def call_tool(
        self, server: str, tool: str, arguments: dict[str, Any] | None = None
    ) -> str:
        if (server, tool) == ("drive", "createTextFile"):
            self.drive_creates += 1
            if self.drive_creates == 1:
                raise TransientToolError(
                    "Drive was busy (synthetic)", outcome_unknown=False
                )
            return f"Created file: {(arguments or {}).get('name')}\nID: {DRIVE_ID}"
        if (server, tool) == ("drive", "listFolder"):
            return (
                f"Contents of folder:\n\n\N{PAGE FACING UP} notes.md (ID: {DRIVE_ID})"
            )
        if server == "notion":
            return PAGE_JSON  # create and read-back both return a page
        raise AssertionError(f"unexpected call {server}.{tool}")


class UsageModel(GenericFakeChatModel):
    """A real LangChain chat model with canned answers and token usage, parsing JSON output."""

    model_name: str = MODEL_NAME

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        return self | PydanticOutputParser(pydantic_object=schema)


def canned_answers() -> list[AIMessage]:
    intent = Intent(
        title="Synthetic workflow",
        summary="A synthetic run used to check tracing.",
        kind="meeting_notes",
        action_items=[ActionItem(task="Check the trace", owner="Alex", due="")],
        decisions=["Tracing works"],
        requested_tools=["notion", "drive", "notifier"],
        clarification_needed="",
    )
    plan = PlannedWorkflow(
        steps=[
            PlannedStep(
                tool="notion",
                action="create_page",
                description="Create the page.",
                params=[PlannedParam(name="title", value="Synthetic", items=[])],
            ),
            PlannedStep(
                tool="drive",
                action="create_text_file",
                description="Save a copy.",
                params=[PlannedParam(name="name", value="notes.md", items=[])],
            ),
            PlannedStep(
                tool="notifier",
                action="send",
                description="Tell the team.",
                params=[PlannedParam(name="title", value="Synthetic notes", items=[])],
            ),
        ]
    )
    usage = {"input_tokens": 120, "output_tokens": 40, "total_tokens": 160}
    meta = {"model_name": MODEL_NAME}
    return [
        AIMessage(
            content=intent.model_dump_json(),
            usage_metadata=usage,
            response_metadata=meta,
        ),
        AIMessage(
            content=plan.model_dump_json(), usage_metadata=usage, response_metadata=meta
        ),
    ]


# --------------------------------------------------------------------------- the run


async def run_synthetic_workflow(
    tracer: Tracer, run_id: str, settings: Settings
) -> str:
    """Run the synthetic workflow and flush. Works with any tracer (a test passes a recorder)."""
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="ok"))
    )
    llm = LlmClient(lambda: UsageModel(messages=iter(canned_answers())), tracer=tracer)
    deps = build_agent_deps(
        settings,
        FakeMcp(),
        EventBus(),
        llm_client=llm,
        http=http,
        simulate="",
        tracer=tracer,
    )
    # The deps label the trace with the settings' real provider and model; this run uses a
    # fake model, so say so rather than let the trace claim otherwise.
    deps.trace_context = {
        "llm_provider": SYNTHETIC_PROVIDER,
        "llm_model": MODEL_NAME,
        "notifier_provider": settings.notifier_provider,
    }
    final = await run_workflow(deps, run_id, REQUEST)
    await asyncio.to_thread(tracer.flush)
    await http.aclose()
    return str(final["status"])


# --------------------------------------------------------------------------- the read-back


def read_observations(tracer: LangfuseTracer, trace_id: str) -> list[Any]:
    found: list[Any] = []
    pages = tracer.client.iter_observation_pages(
        trace_id=trace_id, fields="core,basic,io,usage,model,metadata"
    )
    for page in pages:
        found.extend(page.data)
    return found


def edges_of(observations: list[Any]) -> set[tuple[str, str | None]]:
    names = {o.id: o.name for o in observations}
    return {(o.name, names.get(o.parent_observation_id)) for o in observations}


def wait_for_trace(tracer: LangfuseTracer, trace_id: str) -> list[Any]:
    deadline = time.monotonic() + VERIFY_TIMEOUT_SECONDS
    observations: list[Any] = []
    while time.monotonic() < deadline:
        observations = read_observations(tracer, trace_id)
        if EXPECTED_EDGES <= edges_of(observations):
            return observations
        time.sleep(VERIFY_POLL_SECONDS)
    return observations


def check(observations: list[Any], run_id: str) -> bool:
    results: list[bool] = []
    edges = edges_of(observations)
    missing = sorted(EXPECTED_EDGES - edges)
    results.append(
        report(
            "parent-child structure",
            not missing,
            f"missing: {missing}" if missing else f"{len(edges)} edges",
        )
    )

    roots = [o for o in observations if o.parent_observation_id is None]
    results.append(
        report(
            "one root span",
            [r.name for r in roots] == ["workflow-run"],
            str([r.name for r in roots]),
        )
    )

    drive = [o for o in observations if o.name == "tool:drive.create_text_file"]
    levels = sorted(str(o.level) for o in drive)
    results.append(
        report(
            "retry visible",
            len(drive) == 2 and sum(1 for o in drive if str(o.level) == "ERROR") == 1,
            f"{len(drive)} drive attempts, levels {levels}",
        )
    )

    generations = [o for o in observations if o.name.startswith("llm:")]
    usage_ok = all(
        o.usage_details and sum(o.usage_details.values()) > 0 and o.model == MODEL_NAME
        for o in generations
    )
    results.append(
        report(
            "generations carry model and usage",
            len(generations) == 2 and usage_ok,
            f"{len(generations)} generations",
        )
    )

    root = roots[0] if roots else None
    if root is not None:
        # Only what the read-back API really returns is asserted. The trace NAME and TAGS are
        # not returned by any field group in Langfuse 5.2, so those are checked by eye in the
        # Langfuse UI (see the note printed at the end). Metadata values are never printed: the
        # SDK adds its own entries there, including the (public) key.
        metadata = root.metadata if isinstance(root.metadata, dict) else {}
        attributes_ok = (
            root.session_id == run_id
            and root.version == PROMPT_VERSION
            and str(metadata.get("run_id")) == run_id
            and str(metadata.get("prompt_version")) == PROMPT_VERSION
            and str(metadata.get("llm_provider")) == SYNTHETIC_PROVIDER
        )
        results.append(
            report(
                "trace attributes (session, version, metadata)",
                attributes_ok,
                f"session_ok={root.session_id == run_id}, version={root.version}, "
                f"metadata_run_id_ok={str(metadata.get('run_id')) == run_id}",
            )
        )
    return all(results)


def main() -> int:
    settings = get_settings()
    tracer = create_tracer(settings)
    run_id = uuid.uuid4().hex[:8]
    try:
        if not tracer.client.auth_check():
            report("authenticate", False, "keys rejected; check LANGFUSE_*")
            return 1
        report("authenticate", True, f"host={settings.langfuse_host}")

        status = asyncio.run(run_synthetic_workflow(tracer, run_id, settings))
        report("synthetic run", status == "completed", f"status={status}")
        url = tracer.last_trace_url()
        trace_id = tracer.last_trace_id
        if trace_id is None:
            report("trace id", False, "the run produced no trace id")
            return 1
        observations = wait_for_trace(tracer, trace_id)
        ok = check(observations, run_id) and status == "completed"
        print(f"\nLangfuse trace: {url}")
        print(
            "Check by eye in that page (the read-back API cannot return them): the trace "
            f"name is 'workflow-run' and the tags include 'workflowweaver' and 'run:{run_id}'."
        )
        return 0 if ok else 1
    finally:
        tracer.shutdown()


if __name__ == "__main__":
    sys.exit(main())
