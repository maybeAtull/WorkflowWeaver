import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import anyio
import pytest
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from app.tools.base import FatalToolError, TransientToolError
from app.tools.mcp_session import McpServerSpec, McpSessionManager

SPEC = McpServerSpec(
    name="notion",
    command="docker",
    args=("run",),
    env={},
    allowed_tools=frozenset({"read", "write"}),
    secrets=("TOPSECRET",),
)
OTHER_SPEC = McpServerSpec(
    name="drive",
    command="docker",
    args=("run",),
    env={},
    allowed_tools=frozenset({"list"}),
)

Handler = Callable[[str, dict[str, Any]], Awaitable[Any]]


def reply(text: str, *, is_error: bool = False) -> Any:
    """A stand-in for mcp.types.CallToolResult."""
    return SimpleNamespace(
        isError=is_error, content=[SimpleNamespace(type="text", text=text)]
    )


class FakeBackend:
    """Records server starts and stops, and answers calls via `handler`."""

    def __init__(self, handler: Handler | None = None) -> None:
        self.handler = handler or self._ok
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.start_error: Exception | None = None

    @staticmethod
    async def _ok(tool: str, arguments: dict[str, Any]) -> Any:
        return reply(f"ok:{tool}")

    def factory(self, spec: McpServerSpec):
        backend = self

        @asynccontextmanager
        async def session_cm():
            if backend.start_error is not None:
                raise backend.start_error
            backend.started.append(spec.name)

            class Session:
                async def call_tool(
                    self, name: str, arguments: dict | None = None
                ) -> Any:
                    return await backend.handler(name, arguments or {})

            try:
                yield Session()
            finally:
                backend.stopped.append(spec.name)

        return session_cm()


def manager(
    backend: FakeBackend, *specs: McpServerSpec, **kwargs: Any
) -> McpSessionManager:
    return McpSessionManager(specs or (SPEC, OTHER_SPEC), backend.factory, **kwargs)


async def test_a_server_that_holds_an_anyio_task_group_open_starts_and_closes() -> None:
    """Regression: the real stdio transport keeps an anyio task group (a cancel scope) open
    for the whole session. Entering it inside a start-timeout scope that exits first
    raised 'Attempted to exit a cancel scope that isn't the current task's current cancel
    scope', which a fake without a task group could not reveal."""
    closed: list[str] = []

    class Session:
        async def call_tool(self, name: str, arguments: dict | None = None) -> Any:
            return reply("real-like")

    @asynccontextmanager
    async def factory(spec: McpServerSpec):
        try:
            async with anyio.create_task_group():
                yield Session()
        finally:
            closed.append(spec.name)

    async with McpSessionManager([SPEC], factory) as mcp:
        assert await mcp.call_tool("notion", "read") == "real-like"
        assert await mcp.call_tool("notion", "read") == "real-like"
    assert closed == ["notion"]


async def test_servers_start_lazily_and_only_once() -> None:
    backend = FakeBackend()
    async with manager(backend) as mcp:
        assert backend.started == []  # nothing starts until a tool is used
        assert await mcp.call_tool("notion", "read") == "ok:read"
        assert await mcp.call_tool("notion", "write") == "ok:write"
        assert backend.started == ["notion"]  # reused; "drive" never started


async def test_non_whitelisted_tool_is_blocked_before_anything_starts() -> None:
    backend = FakeBackend()
    async with manager(backend) as mcp:
        with pytest.raises(FatalToolError, match="not on the whitelist"):
            await mcp.call_tool("notion", "delete_everything")
    assert backend.started == []


async def test_unknown_server_is_fatal() -> None:
    async with manager(FakeBackend()) as mcp:
        with pytest.raises(FatalToolError, match="Unknown MCP server"):
            await mcp.call_tool("slack", "read")


@pytest.mark.parametrize(
    "text",
    [
        "429 Too Many Requests",
        "HTTP 503 Service Unavailable",
        "Rate limit exceeded",
        "request timed out",
        "Please try again later",
    ],
)
async def test_temporary_error_text_becomes_transient(text: str) -> None:
    async def handler(tool: str, arguments: dict[str, Any]) -> Any:
        return reply(text, is_error=True)

    async with manager(FakeBackend(handler)) as mcp:
        with pytest.raises(TransientToolError):
            await mcp.call_tool("notion", "read")


@pytest.mark.parametrize(
    "text",
    ["object_not_found (404)", "401 Unauthorized", "invalid_grant", "something odd"],
)
async def test_other_error_text_becomes_fatal(text: str) -> None:
    async def handler(tool: str, arguments: dict[str, Any]) -> Any:
        return reply(text, is_error=True)

    async with manager(FakeBackend(handler)) as mcp:
        with pytest.raises(FatalToolError):
            await mcp.call_tool("notion", "read")


async def test_a_slow_call_times_out_as_transient_and_restarts_the_server() -> None:
    async def handler(tool: str, arguments: dict[str, Any]) -> Any:
        await asyncio.sleep(5)

    backend = FakeBackend(handler)
    async with manager(backend, call_timeout=0.05) as mcp:
        with pytest.raises(TransientToolError, match="timed out"):
            await mcp.call_tool("notion", "read")
        assert backend.stopped == ["notion"]  # dropped, so the next call starts fresh


async def test_a_dead_connection_is_transient_and_the_next_call_restarts() -> None:
    calls = 0

    async def handler(tool: str, arguments: dict[str, Any]) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise anyio.ClosedResourceError
        return reply("back")

    backend = FakeBackend(handler)
    async with manager(backend) as mcp:
        with pytest.raises(TransientToolError, match="connection lost"):
            await mcp.call_tool("notion", "read")
        assert await mcp.call_tool("notion", "read") == "back"
    assert backend.started == ["notion", "notion"]


async def test_a_timeout_leaves_the_outcome_unknown() -> None:
    async def handler(tool: str, arguments: dict[str, Any]) -> Any:
        await asyncio.sleep(5)

    async with manager(FakeBackend(handler), call_timeout=0.05) as mcp:
        with pytest.raises(TransientToolError) as exc_info:
            await mcp.call_tool("notion", "read")
    assert (
        exc_info.value.outcome_unknown is True
    )  # the request was sent; it may have run


@pytest.mark.parametrize(
    ("text", "outcome_unknown"),
    [
        ("429 Too Many Requests", False),  # "not now": provably did not run
        ("Rate limit exceeded", False),
        ("HTTP 503 Service Unavailable", True),
        ("request timed out", True),
        ("Please try again later", True),
    ],
)
async def test_only_rate_limit_replies_are_known_not_to_have_run(
    text: str, outcome_unknown: bool
) -> None:
    async def handler(tool: str, arguments: dict[str, Any]) -> Any:
        return reply(text, is_error=True)

    async with manager(FakeBackend(handler)) as mcp:
        with pytest.raises(TransientToolError) as exc_info:
            await mcp.call_tool("notion", "read")
    assert exc_info.value.outcome_unknown is outcome_unknown


async def test_a_lost_connection_leaves_the_outcome_unknown() -> None:
    async def handler(tool: str, arguments: dict[str, Any]) -> Any:
        raise anyio.ClosedResourceError

    async with manager(FakeBackend(handler)) as mcp:
        with pytest.raises(TransientToolError) as exc_info:
            await mcp.call_tool("notion", "read")
    assert exc_info.value.outcome_unknown is True


async def test_a_start_timeout_is_known_not_to_have_run() -> None:
    @asynccontextmanager
    async def slow_factory(spec: McpServerSpec):
        await asyncio.sleep(5)
        yield None

    async with McpSessionManager([SPEC], slow_factory, start_timeout=0.05) as mcp:
        with pytest.raises(
            TransientToolError, match="Starting notion timed out"
        ) as exc_info:
            await mcp.call_tool("notion", "read")
    assert exc_info.value.outcome_unknown is False  # no tool call was ever sent


async def test_a_protocol_level_rejection_is_fatal() -> None:
    async def handler(tool: str, arguments: dict[str, Any]) -> Any:
        raise McpError(ErrorData(code=-32602, message="Invalid params"))

    async with manager(FakeBackend(handler)) as mcp:
        with pytest.raises(FatalToolError, match="rejected the request"):
            await mcp.call_tool("notion", "read")


async def test_start_failure_is_fatal_mentions_docker_and_is_retried_next_time() -> (
    None
):
    backend = FakeBackend()
    backend.start_error = FileNotFoundError("docker not found")
    async with manager(backend) as mcp:
        with pytest.raises(FatalToolError, match="Docker Desktop"):
            await mcp.call_tool("notion", "read")
        backend.start_error = None
        assert await mcp.call_tool("notion", "read") == "ok:read"


async def test_exit_closes_every_started_server_even_if_the_run_fails() -> None:
    backend = FakeBackend()
    with pytest.raises(RuntimeError, match="run failed"):
        async with manager(backend) as mcp:
            await mcp.call_tool("notion", "read")
            await mcp.call_tool("drive", "list")
            raise RuntimeError("run failed")
    assert sorted(backend.stopped) == ["drive", "notion"]


async def test_secrets_are_scrubbed_from_error_messages() -> None:
    async def handler(tool: str, arguments: dict[str, Any]) -> Any:
        return reply("failed for token TOPSECRET", is_error=True)

    async with manager(FakeBackend(handler)) as mcp:
        with pytest.raises(FatalToolError) as exc_info:
            await mcp.call_tool("notion", "read")
    assert "TOPSECRET" not in str(exc_info.value)
    assert "***" in str(exc_info.value)


async def test_arguments_reach_the_server() -> None:
    seen: dict[str, Any] = {}

    async def handler(tool: str, arguments: dict[str, Any]) -> Any:
        seen.update(arguments)
        return reply("ok")

    async with manager(FakeBackend(handler)) as mcp:
        await mcp.call_tool("notion", "write", {"title": "Plan"})
    assert seen == {"title": "Plan"}
