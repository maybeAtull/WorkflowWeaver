from typing import Any, ClassVar

import pytest

from app.agent import runtime
from app.agent.llm_client import LlmClient
from app.agent.runtime import agent_runtime, build_agent_deps
from app.config import Settings
from app.events import EventBus
from app.observability.tracing import InMemoryTracer
from tests.test_config import FAKE_ENV


class NoMcp:
    async def call_tool(self, *args: Any, **kwargs: Any) -> str:
        raise AssertionError("wiring must not call any tool")


def make_settings(monkeypatch: pytest.MonkeyPatch, simulate: str = "") -> Settings:
    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("SIMULATE_FAILURE_STEP", simulate)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def never_built_client() -> LlmClient:
    def factory() -> Any:
        raise AssertionError("wiring must not build or call the LLM")

    return LlmClient(factory)


def deps_for(settings: Settings, **kw: Any) -> Any:
    return build_agent_deps(
        settings, NoMcp(), EventBus(), llm_client=never_built_client(), **kw
    )


def test_the_deps_hold_the_three_tools_and_the_two_llm_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps = deps_for(make_settings(monkeypatch))
    for tool in ("notion", "drive", "notifier"):
        assert deps.registry.adapter(tool).name == tool
    assert callable(deps.planner) and callable(deps.intent_parser)


def test_building_the_deps_touches_neither_the_llm_nor_any_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps_for(make_settings(monkeypatch))  # NoMcp and the client factory would raise


def test_failure_injection_comes_from_the_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps = deps_for(make_settings(monkeypatch, "s2:transient"))
    assert (deps.injection.step_ref, deps.injection.mode) == ("s2", "transient")


def test_an_explicit_override_wins_and_an_empty_one_turns_injection_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(monkeypatch, "s2:transient")
    assert deps_for(settings, simulate="s1:fatal").injection.step_ref == "s1"
    assert deps_for(settings, simulate="").injection is None


def test_no_injection_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    assert deps_for(make_settings(monkeypatch)).injection is None


class FakeManager:
    """Stands in for McpSessionManager: records entering and leaving."""

    instances: ClassVar[list["FakeManager"]] = []

    def __init__(self, specs: Any) -> None:
        self.entered = self.exited = False
        FakeManager.instances.append(self)

    async def __aenter__(self) -> "NoMcp":
        self.entered = True
        return NoMcp()

    async def __aexit__(self, *exc: object) -> None:
        self.exited = True


@pytest.fixture
def fake_manager(monkeypatch: pytest.MonkeyPatch) -> type[FakeManager]:
    FakeManager.instances = []
    monkeypatch.setattr(runtime, "McpSessionManager", FakeManager)
    monkeypatch.setattr(runtime, "default_specs", lambda settings: [])
    return FakeManager


async def test_the_runtime_opens_one_manager_and_closes_it(
    monkeypatch: pytest.MonkeyPatch, fake_manager: type[FakeManager]
) -> None:
    settings = make_settings(monkeypatch)
    async with agent_runtime(
        settings, EventBus(), llm_client=never_built_client()
    ) as deps:
        assert len(fake_manager.instances) == 1
        assert (
            fake_manager.instances[0].entered and not fake_manager.instances[0].exited
        )
        assert deps.registry.adapter("notion")
    assert fake_manager.instances[0].exited


async def test_the_manager_is_closed_even_when_the_run_fails(
    monkeypatch: pytest.MonkeyPatch, fake_manager: type[FakeManager]
) -> None:
    settings = make_settings(monkeypatch)
    with pytest.raises(RuntimeError, match="boom"):
        async with agent_runtime(settings, EventBus(), llm_client=never_built_client()):
            raise RuntimeError("boom")
    assert fake_manager.instances[0].exited


async def test_the_trace_is_flushed_once_when_the_run_ends(
    monkeypatch: pytest.MonkeyPatch, fake_manager: type[FakeManager]
) -> None:
    tracer = InMemoryTracer()
    settings = make_settings(monkeypatch)
    async with agent_runtime(
        settings, EventBus(), llm_client=never_built_client(), tracer=tracer
    ):
        assert tracer.flushes == 0  # nothing is sent while the run is going
    assert tracer.flushes == 1


async def test_the_trace_is_flushed_even_when_the_run_fails(
    monkeypatch: pytest.MonkeyPatch, fake_manager: type[FakeManager]
) -> None:
    tracer = InMemoryTracer()
    settings = make_settings(monkeypatch)
    with pytest.raises(RuntimeError, match="boom"):
        async with agent_runtime(
            settings, EventBus(), llm_client=never_built_client(), tracer=tracer
        ):
            raise RuntimeError("boom")
    assert tracer.flushes == 1 and fake_manager.instances[0].exited


async def test_a_flush_that_fails_does_not_fail_the_run(
    monkeypatch: pytest.MonkeyPatch, fake_manager: type[FakeManager]
) -> None:
    class BrokenFlush(InMemoryTracer):
        def flush(self) -> None:
            raise RuntimeError("langfuse down")

    settings = make_settings(monkeypatch)
    async with agent_runtime(
        settings, EventBus(), llm_client=never_built_client(), tracer=BrokenFlush()
    ):
        pass  # leaving the block must not raise


def test_the_deps_carry_the_tracer_and_the_provider_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracer = InMemoryTracer()
    deps = deps_for(make_settings(monkeypatch), tracer=tracer)
    assert deps.tracer is not None
    assert set(deps.trace_context) == {"llm_provider", "llm_model", "notifier_provider"}
