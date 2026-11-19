"""The live trace check's fakes and assertions, proven offline, so the live run tests only Langfuse.

The synthetic workflow runs here against the in-memory recorder: if its tree contains every edge
the live check will look for, then a failure against real Langfuse can only be about Langfuse.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from app.config import Settings
from app.observability.tracing import InMemoryTracer, SpanRecord
from scripts import smoke_trace
from scripts.smoke_trace import (
    EXPECTED_EDGES,
    MODEL_NAME,
    check,
    edges_of,
    run_synthetic_workflow,
)
from tests.test_config import FAKE_ENV


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def recorded_edges(tracer: InMemoryTracer) -> set[tuple[str, str | None]]:
    edges: set[tuple[str, str | None]] = set()

    def walk(span: SpanRecord, parent: str | None) -> None:
        edges.add((span.name, parent))
        for child in span.children:
            walk(child, span.name)

    for root in tracer.roots:
        walk(root, None)
    return edges


async def test_the_synthetic_workflow_completes_and_has_every_expected_edge(
    settings: Settings,
) -> None:
    tracer = InMemoryTracer()
    status = await run_synthetic_workflow(tracer, "abc12345", settings)
    assert status == "completed"
    assert EXPECTED_EDGES <= recorded_edges(tracer)
    assert tracer.flushes == 1


async def test_the_synthetic_workflow_has_a_retry_and_real_token_usage(
    settings: Settings,
) -> None:
    tracer = InMemoryTracer()
    await run_synthetic_workflow(tracer, "abc12345", settings)
    root = tracer.roots[0]
    drive = root.find("tool:drive.create_text_file")
    assert [s.level for s in drive] == ["ERROR", None]
    generations = root.find("llm:parse_intent") + root.find("llm:plan")
    assert len(generations) == 2
    assert all(
        g.model == MODEL_NAME and g.usage == {"input": 120, "output": 40, "total": 160}
        for g in generations
    )
    assert tracer.trace["tags"][:2] == ["workflowweaver", "run:abc12345"]
    # The trace is labelled as synthetic, not with the settings' real provider.
    assert tracer.trace["metadata"]["llm_provider"] == "synthetic"
    assert "llm:synthetic" in tracer.trace["tags"]


# ------------------------------------------------------------------ the assertions themselves


def observation(
    ident: str, name: str, parent: str | None, **extra: Any
) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "id": ident,
        "name": name,
        "parent_observation_id": parent,
        "level": "DEFAULT",
        "usage_details": None,
        "model": None,
        "metadata": None,
        "session_id": None,
        "version": None,
    }
    fields.update(extra)
    return SimpleNamespace(**fields)


def good_trace(run_id: str = "r1") -> list[SimpleNamespace]:
    """What a correct trace looks like when read back from Langfuse."""
    names = {name for edge in EXPECTED_EDGES for name in edge}
    ids = {name: f"id-{i}" for i, name in enumerate(sorted(names))}
    out = []
    for child, parent in sorted(EXPECTED_EDGES):
        extra: dict[str, Any] = {}
        if child.startswith("llm:"):
            extra = {"usage_details": {"total": 160}, "model": MODEL_NAME}
        out.append(observation(ids[child], child, ids[parent], **extra))
    out.append(
        observation(
            ids["workflow-run"],
            "workflow-run",
            None,
            session_id=run_id,
            version=smoke_trace.PROMPT_VERSION,
            metadata={
                "run_id": run_id,
                "prompt_version": smoke_trace.PROMPT_VERSION,
                "llm_provider": smoke_trace.SYNTHETIC_PROVIDER,
            },
        )
    )
    out.append(
        observation(
            "retry", "tool:drive.create_text_file", ids["execute_step"], level="ERROR"
        )
    )
    return out


def test_a_correct_trace_passes_every_check(capsys: pytest.CaptureFixture[str]) -> None:
    assert check(good_trace(), "r1") is True
    assert "FAIL" not in capsys.readouterr().out


def test_edges_are_derived_from_parent_ids() -> None:
    edges = edges_of([observation("1", "root", None), observation("2", "child", "1")])
    assert edges == {("root", None), ("child", "root")}


def test_a_flat_trace_fails_the_structure_check(
    capsys: pytest.CaptureFixture[str],
) -> None:
    flat = [
        observation(str(i), name, None)
        for i, name in enumerate(["workflow-run", "execute_step"])
    ]
    assert check(flat, "r1") is False
    out = capsys.readouterr().out
    assert "FAIL] parent-child structure" in out and "FAIL] one root span" in out


def test_a_missing_retry_is_reported() -> None:
    trace = [o for o in good_trace() if o.id != "retry"]
    assert check(trace, "r1") is False


def test_missing_usage_or_wrong_attributes_fail() -> None:
    no_usage = good_trace()
    for o in no_usage:
        if o.name.startswith("llm:"):
            o.usage_details = None
    assert check(no_usage, "r1") is False
    assert check(good_trace("r1"), "other-run") is False  # the session id must match


def test_the_trace_metadata_must_match_the_run() -> None:
    trace = good_trace("r1")
    root = next(o for o in trace if o.parent_observation_id is None)
    root.metadata = {
        "run_id": "someone-else",
        "prompt_version": "2",
        "llm_provider": "synthetic",
    }
    assert check(trace, "r1") is False


def test_metadata_values_are_never_printed(capsys: pytest.CaptureFixture[str]) -> None:
    trace = good_trace("r1")
    root = next(o for o in trace if o.parent_observation_id is None)
    root.metadata["scope.attributes.public_key"] = "pk-lf-should-not-appear"
    check(trace, "r1")
    assert "pk-lf" not in capsys.readouterr().out
