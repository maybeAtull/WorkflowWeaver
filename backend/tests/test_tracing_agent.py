"""The agent's instrumentation: the exact span tree of each kind of run, with fake tools."""

from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.output_parsers import PydanticOutputParser

from app.agent.failure_injection import Injection
from app.agent.llm_client import LlmClient, LlmError, usage_from_metadata
from app.agent.prompts import PROMPT_VERSION
from app.observability.tracing import InMemoryTracer, SafeTracer, SpanRecord
from app.tools.base import (
    FatalToolError,
    TransientToolError,
    VerifyResult,
)
from tests.test_graph import Rig, make, notify
from tests.test_llm_client import Answer, FakeModel, StatusError
from tests.test_tracing import ExplodingTracer


def tree(tracer: InMemoryTracer) -> Any:
    assert len(tracer.roots) == 1
    return tracer.roots[0].shape()


def children(tracer: InMemoryTracer, name: str) -> list[SpanRecord]:
    return tracer.roots[0].find(name)


# ------------------------------------------------------------------ the tree of a run


async def test_a_successful_run_is_one_trace_with_a_span_per_node_and_per_tool_call() -> (
    None
):
    tracer = InMemoryTracer()
    await Rig([make("a"), make("b"), notify()], tracer=tracer).run()
    assert tree(tracer) == (
        "workflow-run",
        [
            ("parse_intent", []),
            ("plan_steps", []),
            ("execute_step", [("tool:fake.make", [])]),
            ("execute_step", [("tool:fake.make", [])]),
            # Both items are read back BEFORE the irreversible notification goes out.
            ("verify_work", [("verify:fake.make", []), ("verify:fake.make", [])]),
            ("execute_step", [("tool:fake.notify", [])]),
            ("write_report", []),
        ],
    )


async def test_tool_spans_carry_the_step_input_and_result() -> None:
    tracer = InMemoryTracer()
    await Rig([make("a"), notify()], tracer=tracer).run()
    [tool] = children(tracer, "tool:fake.make")
    assert tool.type == "tool"
    assert tool.input == {"title": "a"} and tool.metadata == {
        "step_id": "s-a",
        "attempt": 1,
    }
    assert tool.output["summary"] == "make a"
    assert tool.output["url"] == "https://example.test/a"
    assert tool.level is None


async def test_node_spans_summarise_what_the_node_did() -> None:
    tracer = InMemoryTracer()
    await Rig([make("a"), notify()], tracer=tracer).run()
    first_step = children(tracer, "execute_step")[0]
    assert first_step.input == {
        "status": "running",
        "current_idx": 0,
        "steps_in_plan": 2,
    }
    assert first_step.output == {
        "status": "verifying",  # the irreversible notification is next
        "current_idx": 1,
        "retries": 0,
        "completed_added": 1,
    }


async def test_long_tool_parameters_are_clipped_in_the_trace_but_not_in_the_call() -> (
    None
):
    tracer = InMemoryTracer()
    rig = Rig([make("x" * 3000), notify()], tracer=tracer)
    await rig.run()
    [tool] = children(tracer, "tool:fake.make")
    assert "truncated" in tool.input["title"] and len(tool.input["title"]) < 2100
    assert rig.adapter.calls[0] == "x" * 3000  # the real call got the full value


# ------------------------------------------------------------------ the trace itself


async def test_the_root_carries_the_run_id_providers_and_prompt_version() -> None:
    tracer = InMemoryTracer()
    rig = Rig(
        [make("a")],
        tracer=tracer,
        trace_context={
            "llm_provider": "groq",
            "llm_model": "m1",
            "notifier_provider": "slack",
        },
    )
    await rig.run()
    assert tracer.trace["run_id"] == "r"
    assert tracer.trace["tags"] == [
        "workflowweaver",
        "run:r",
        "llm:groq",
        "notifier:slack",
    ]
    assert tracer.trace["metadata"] == {
        "run_id": "r",
        "prompt_version": PROMPT_VERSION,
        "llm_provider": "groq",
        "llm_model": "m1",
        "notifier_provider": "slack",
    }
    assert tracer.trace["input"] == "do the thing"


async def test_the_root_records_the_outcome_and_the_scores_match_it() -> None:
    tracer = InMemoryTracer()
    await Rig([make("a"), notify()], tracer=tracer).run()
    root = tracer.roots[0]
    assert root.output["status"] == "completed"
    assert root.output["headline"].startswith("Done: all 2 steps finished")
    scores = dict(tracer.scores)
    assert scores["run_status"] == "completed" and scores["retries"] == 0.0
    assert (
        isinstance(scores["duration_seconds"], float)
        and scores["duration_seconds"] >= 0
    )


async def test_a_failed_run_is_scored_as_such() -> None:
    tracer = InMemoryTracer()
    await Rig([make("a")], fail={"a": [FatalToolError("boom")]}, tracer=tracer).run()
    assert dict(tracer.scores)["run_status"] == "failed"


# ------------------------------------------------------------------ retries, failures, rollback


async def test_every_retry_attempt_is_its_own_tool_span() -> None:
    err = TransientToolError("busy", outcome_unknown=False)
    tracer = InMemoryTracer()
    rig = Rig([make("a"), notify()], fail={"a": [err, err]}, tracer=tracer)
    final = await rig.run()
    spans = children(tracer, "tool:fake.make")
    assert [s.metadata["attempt"] for s in spans] == [1, 2, 3]
    assert [s.level for s in spans] == ["ERROR", "ERROR", None]
    assert spans[0].status_message == "busy"
    assert dict(tracer.scores)["retries"] == 2.0 == float(final["retries"])


async def test_an_injected_failure_shows_up_like_a_real_one() -> None:
    tracer = InMemoryTracer()
    rig = Rig(
        [make("a"), make("b"), notify()],
        injection=Injection("s-b", "fatal"),
        tracer=tracer,
    )
    await rig.run()
    failed = [s for s in children(tracer, "tool:fake.make") if s.level == "ERROR"]
    assert len(failed) == 1
    assert "Injected failure" in (failed[0].status_message or "")
    assert rig.adapter.calls == ["a"]  # and the real tool was never reached


async def test_rollback_spans_follow_the_reverse_order_of_the_undo() -> None:
    tracer = InMemoryTracer()
    rig = Rig(
        [make("a"), make("b"), make("c")],
        fail={"c": [FatalToolError("boom")]},
        tracer=tracer,
    )
    await rig.run()
    [roll_back] = children(tracer, "roll_back")
    assert [s.name for s in roll_back.children] == ["undo:fake.make", "undo:fake.make"]
    assert [s.input["step_id"] for s in roll_back.children] == ["s-b", "s-a"]
    assert all(s.output == {"undone": True} for s in roll_back.children)


async def test_a_failed_undo_is_an_error_span() -> None:
    tracer = InMemoryTracer()
    rig = Rig(
        [make("a"), make("b")],
        fail={"b": [FatalToolError("boom")]},
        undo_fail={"a": [FatalToolError("cannot delete")]},
        tracer=tracer,
    )
    await rig.run()
    [undo] = children(tracer, "undo:fake.make")
    assert undo.level == "ERROR" and undo.status_message == "cannot delete"


async def test_a_failed_check_is_a_warning_and_the_message_is_never_sent() -> None:
    tracer = InMemoryTracer()
    rig = Rig(
        [make("a"), make("b"), notify()],
        verify={"b": VerifyResult(status="failed", detail="The page is in the trash.")},
        tracer=tracer,
    )
    await rig.run()
    checks = children(tracer, "verify:fake.make")
    assert [c.level for c in checks] == [None, "WARNING"]
    assert checks[1].output == {
        "status": "failed",
        "detail": "The page is in the trash.",
    }
    assert children(tracer, "tool:fake.notify") == []  # nothing was announced


async def test_a_bug_in_a_tool_records_only_its_type() -> None:
    tracer = InMemoryTracer()
    rig = Rig(
        [make("a")], fail={"a": [RuntimeError("secret user text")]}, tracer=tracer
    )
    await rig.run()
    [tool] = children(tracer, "tool:fake.make")
    assert tool.status_message == "RuntimeError"
    assert "secret user text" not in repr(tracer.roots)


# ------------------------------------------------------------------ safety


async def test_a_tracing_backend_that_fails_everywhere_changes_nothing() -> None:
    plan = [make("a"), make("b"), notify()]
    quiet = Rig(plan)
    expected = await quiet.run()
    loud = Rig(plan, tracer=ExplodingTracer())  # type: ignore[arg-type]
    final = await loud.run()
    assert final["status"] == expected["status"] == "completed"
    assert loud.types() == quiet.types()
    assert loud.adapter.calls == quiet.adapter.calls
    assert final["report"]["headline"] == expected["report"]["headline"]


def test_any_tracer_given_to_the_deps_is_made_safe() -> None:
    assert isinstance(Rig([make("a")], tracer=ExplodingTracer()).deps.tracer, SafeTracer)  # type: ignore[arg-type]


# ------------------------------------------------------------------ LLM spans


class NamedModel(FakeModel):
    model_name = "fake-model"


def llm(model: Any, tracer: InMemoryTracer) -> LlmClient:
    async def no_sleep(seconds: float) -> None:
        return None

    return LlmClient(lambda: model, no_sleep, float, tracer=tracer)


async def test_an_llm_call_is_a_generation_span_with_prompt_and_answer() -> None:
    tracer = InMemoryTracer()
    await llm(NamedModel(Answer(word="ok")), tracer).ask(
        "be brief", "say ok", Answer, name="parse_intent"
    )
    [span] = tracer.roots
    assert (span.name, span.type, span.model) == (
        "llm:parse_intent",
        "generation",
        "fake-model",
    )
    assert span.input == [
        {"role": "system", "content": "be brief"},
        {"role": "human", "content": "say ok"},
    ]
    assert span.output == {"word": "ok"} and span.metadata == {"attempt": 1}
    assert span.usage is None  # the provider reported none, and none is invented


async def test_each_llm_retry_is_its_own_generation_span() -> None:
    tracer = InMemoryTracer()
    model = NamedModel(TimeoutError(), TimeoutError(), Answer(word="ok"))
    await llm(model, tracer).ask("s", "h", Answer, name="plan")
    assert [(s.metadata["attempt"], s.level) for s in tracer.roots] == [
        (1, "ERROR"),
        (2, "ERROR"),
        (3, None),
    ]
    assert tracer.roots[0].status_message == "transient"


async def test_a_failed_llm_span_holds_only_the_classification_never_the_message() -> (
    None
):
    tracer = InMemoryTracer()
    model = NamedModel(
        StatusError("API key sk-SECRET rejected for confidential text", 401)
    )
    with pytest.raises(LlmError):
        await llm(model, tracer).ask("s", "h", Answer)
    [span] = tracer.roots
    assert span.status_message == "auth"
    assert "sk-SECRET" not in repr(
        tracer.roots
    )  # the prompts hold none, the error text is dropped


class UsageModel(GenericFakeChatModel):
    """A real LangChain chat model (so the usage callback really fires) that parses JSON."""

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        return self | PydanticOutputParser(pydantic_object=schema)


async def test_token_usage_reported_by_the_model_lands_on_the_span() -> None:
    message = AIMessage(
        content='{"word": "ok"}',
        usage_metadata={"input_tokens": 11, "output_tokens": 5, "total_tokens": 16},
        response_metadata={"model_name": "fake-model"},
    )
    tracer = InMemoryTracer()
    client = llm(UsageModel(messages=iter([message])), tracer)
    assert await client.ask("s", "h", Answer, name="plan") == Answer(word="ok")
    assert tracer.roots[0].usage == {"input": 11, "output": 5, "total": 16}


def test_usage_is_summed_over_models_and_empty_means_none() -> None:
    assert usage_from_metadata({}) is None
    assert usage_from_metadata(
        {
            "a": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
            "b": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
        }
    ) == {"input": 11, "output": 22, "total": 33}


async def test_llm_calls_nest_under_the_node_that_made_them() -> None:
    """With a node that calls the client, the generation sits inside the node's span."""
    tracer = InMemoryTracer()
    rig = Rig([make("a")], tracer=tracer)
    client = llm(NamedModel(Answer(word="ok")), tracer)
    inner = rig.deps.planner

    async def planner_that_asks(state: Any) -> Any:
        await client.ask("s", "h", Answer, name="plan")
        return await inner(state)

    rig.deps.planner = planner_that_asks
    await rig.run()
    [plan_span] = children(tracer, "plan_steps")
    assert [c.name for c in plan_span.children] == ["llm:plan"]
