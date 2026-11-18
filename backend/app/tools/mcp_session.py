"""Long-lived MCP sessions for one workflow run.

Starting an MCP server (a Docker container) takes seconds, so a run opens each server once,
lazily on first use, and reuses it for every step. The manager also enforces a per-server
tool whitelist and turns provider failures into our two error kinds (transient: retry,
fatal: roll back).

Usage (one `async with` per workflow run):

    async with McpSessionManager(default_specs(settings)) as mcp:
        text = await mcp.call_tool("notion", "API-retrieve-a-page", {"page_id": "..."})

Why each server runs in its own task: the MCP stdio transport keeps an anyio task group
(a cancel scope) open for the whole session, and cancel scopes must be entered and exited in
strict nesting order by the same task. So a small runner task opens the connection, holds it
open until told to stop, and closes it, all by itself. Everything else just waits on a
"ready" signal with an ordinary timeout, with no cancel scope wrapped around the connection.
"""

import asyncio
import logging
import re
from collections.abc import Callable, Iterable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

import anyio
from langchain_mcp_adapters.client import MultiServerMCPClient
from mcp.shared.exceptions import McpError

from app.tools.base import FatalToolError, TransientToolError

logger = logging.getLogger(__name__)

CALL_TIMEOUT_SECONDS = 60
START_TIMEOUT_SECONDS = 120
SHUTDOWN_TIMEOUT_SECONDS = 15
MAX_ERROR_CHARS = 500


@dataclass(frozen=True)
class McpServerSpec:
    """How to start one MCP server and which of its tools we allow."""

    name: str
    command: str
    args: tuple[str, ...]
    env: dict[str, str]
    allowed_tools: frozenset[str]
    # Values (tokens) to scrub from error messages before they reach logs or the UI.
    secrets: tuple[str, ...] = field(default=(), repr=False)


class McpSession(Protocol):
    """The slice of mcp.ClientSession we use (the real one has extra optional arguments)."""

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> Any: ...


SessionFactory = Callable[[McpServerSpec], AbstractAsyncContextManager[McpSession]]


@asynccontextmanager
async def open_stdio_session(spec: McpServerSpec):
    """Production factory: start the server as a subprocess and speak MCP over stdio."""
    client = MultiServerMCPClient(
        {
            spec.name: {
                "transport": "stdio",
                "command": spec.command,
                "args": list(spec.args),
                "env": dict(spec.env),
            }
        }
    )
    async with client.session(spec.name) as session:
        yield session


@dataclass
class _RunningServer:
    session: McpSession
    stop: asyncio.Event  # set to ask the runner task to close the connection
    task: "asyncio.Task[None]"


# MCP returns errors as text, so we classify by wording. Only clearly temporary conditions
# are retried: retrying a create that actually succeeded would duplicate it, so anything
# unrecognised is treated as fatal (the safe default).
_TRANSIENT = re.compile(
    r"\b(429|500|502|503|504)\b|rate.?limit|too many requests|timed? ?out|timeout"
    r"|temporar|unavailable|overloaded|econnreset|etimedout|socket hang up|try again",
    re.IGNORECASE,
)

# The server process died or its pipe closed: restart it on the next call.
_CONNECTION_ERRORS = (
    anyio.ClosedResourceError,
    anyio.BrokenResourceError,
    anyio.EndOfStream,
    BrokenPipeError,
    ConnectionError,
    EOFError,
)


def _result_text(result: Any) -> str:
    blocks = getattr(result, "content", None) or []
    texts = [
        getattr(b, "text", "") for b in blocks if getattr(b, "type", None) == "text"
    ]
    return "\n".join(t for t in texts if t)


class McpSessionManager:
    def __init__(
        self,
        specs: Iterable[McpServerSpec],
        session_factory: SessionFactory = open_stdio_session,
        call_timeout: float = CALL_TIMEOUT_SECONDS,
        start_timeout: float = START_TIMEOUT_SECONDS,
    ) -> None:
        self._specs = {spec.name: spec for spec in specs}
        self._factory = session_factory
        self._call_timeout = call_timeout
        self._start_timeout = start_timeout
        self._running: dict[str, _RunningServer] = {}
        self._locks = {name: asyncio.Lock() for name in self._specs}

    # typing.Self needs Python 3.11; the brief allows 3.10, so the class is named explicitly.
    async def __aenter__(self) -> "McpSessionManager":  # noqa: PYI034
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close every server we started. One failure never stops the others closing."""
        for name in list(self._running):
            await self._close_server(name)

    async def call_tool(
        self, server: str, tool: str, arguments: dict[str, Any] | None = None
    ) -> str:
        """Call a whitelisted tool and return its text. Raises TransientToolError or FatalToolError."""
        spec = self._specs.get(server)
        if spec is None:
            raise FatalToolError(f"Unknown MCP server {server!r}")
        # Checked before anything starts: a blocked call must never launch a container.
        if tool not in spec.allowed_tools:
            raise FatalToolError(f"Tool {server}.{tool} is not on the whitelist")

        session = await self._ensure_started(spec)
        try:
            with anyio.fail_after(self._call_timeout):
                result = await session.call_tool(tool, arguments or {})
        except TimeoutError as exc:
            # A cancelled in-flight request can leave the session out of step: start fresh.
            await self._close_server(server)
            raise TransientToolError(
                f"{server}.{tool} timed out after {self._call_timeout:g}s"
            ) from exc
        except _CONNECTION_ERRORS as exc:
            await self._close_server(server)
            raise TransientToolError(
                self._clean(
                    spec, f"{server} connection lost ({type(exc).__name__}): {exc}"
                )
            ) from exc
        except McpError as exc:
            raise FatalToolError(
                self._clean(spec, f"{server}.{tool} rejected the request: {exc}")
            ) from exc
        # Unknown failures are fatal and never retried, so catching broadly is intended.
        except Exception as exc:
            raise FatalToolError(
                self._clean(
                    spec, f"{server}.{tool} failed ({type(exc).__name__}): {exc}"
                )
            ) from exc

        text = _result_text(result)
        if getattr(result, "isError", False):
            message = self._clean(spec, f"{server}.{tool} returned an error: {text}")
            if _TRANSIENT.search(text):
                raise TransientToolError(message)
            raise FatalToolError(message)
        return text

    async def _ensure_started(self, spec: McpServerSpec) -> McpSession:
        async with self._locks[spec.name]:
            running = self._running.get(spec.name)
            if running is not None:
                return running.session

            ready: asyncio.Future[McpSession] = (
                asyncio.get_running_loop().create_future()
            )
            stop = asyncio.Event()

            async def run_server() -> None:
                # The connection is opened, held and closed entirely inside this one task.
                try:
                    async with self._factory(spec) as session:
                        if not ready.done():
                            ready.set_result(session)
                        await stop.wait()
                except asyncio.CancelledError:
                    if not ready.done():
                        ready.cancel()
                    raise
                except Exception as exc:
                    if ready.done():
                        logger.warning(
                            "MCP server %s stopped unexpectedly",
                            spec.name,
                            exc_info=True,
                        )
                    else:
                        ready.set_exception(exc)  # reported to the caller below

            task = asyncio.create_task(run_server(), name=f"mcp-{spec.name}")
            try:
                # shield: a timeout must not cancel `ready` itself, only our wait on it.
                session = await asyncio.wait_for(
                    asyncio.shield(ready), self._start_timeout
                )
            # Python 3.10 has two distinct timeout classes (they are aliases from 3.11).
            except (TimeoutError, asyncio.TimeoutError) as exc:  # noqa: UP041
                await self._stop_task(task, stop)
                raise TransientToolError(
                    f"Starting {spec.name} timed out after {self._start_timeout:g}s"
                ) from exc
            except asyncio.CancelledError:
                task.cancel()
                raise
            # Any startup failure is reported (never hidden), so catching broadly is intended.
            except Exception as exc:
                await self._stop_task(task, stop)
                raise FatalToolError(
                    self._clean(
                        spec,
                        f"Could not start {spec.name} ({type(exc).__name__}): {exc}. "
                        "Is Docker Desktop running and the image available?",
                    )
                ) from exc

            self._running[spec.name] = _RunningServer(session, stop, task)
            return session

    async def _close_server(self, name: str) -> None:
        running = self._running.pop(name, None)
        if running is not None:
            await self._stop_task(running.task, running.stop)

    @staticmethod
    async def _stop_task(task: "asyncio.Task[None]", stop: asyncio.Event) -> None:
        """Ask a runner task to close its connection, and force it if it will not."""
        stop.set()
        done, _ = await asyncio.wait({task}, timeout=SHUTDOWN_TIMEOUT_SECONDS)
        if not done:
            logger.warning(
                "MCP server task %s did not stop in time; cancelling", task.get_name()
            )
            task.cancel()
            await asyncio.wait({task}, timeout=SHUTDOWN_TIMEOUT_SECONDS)
        if task.done() and not task.cancelled() and task.exception() is not None:
            logger.warning(
                "MCP server task %s ended with an error",
                task.get_name(),
                exc_info=task.exception(),
            )

    @staticmethod
    def _clean(spec: McpServerSpec, message: str) -> str:
        for secret in spec.secrets:
            if secret:
                message = message.replace(secret, "***")
        return message[:MAX_ERROR_CHARS]
