"""The contract every tool adapter follows.

The agent never calls Notion, Drive, Slack or Teams directly; it goes through a ToolAdapter.
That gives the graph one uniform way to (1) run an action, (2) record how to undo it, and
(3) tell a retryable failure from a fatal one, which is what retry and rollback depend on.
"""

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# --------------------------------------------------------------------------- errors


class ToolError(Exception):
    """Base class for tool failures. Adapters raise these instead of returning a failure."""


class TransientToolError(ToolError):
    """Temporary failure (timeout, rate limit, 5xx). The agent retries with backoff."""


class FatalToolError(ToolError):
    """Failure that retrying cannot fix (bad input, auth, missing resource). Triggers rollback."""


class ToolContractError(FatalToolError):
    """An adapter broke the contract (a bug in the adapter, not a runtime failure)."""


# --------------------------------------------------------------------------- data


class UndoHandle(BaseModel):
    """Everything needed to reverse one executed action.

    Plain strings only, so it is JSON-serializable: it goes into the agent state, can be
    stored in SQLite and is shown in the execution report.
    """

    tool: str
    action: str
    data: dict[str, str] = Field(default_factory=dict)  # e.g. {"page_id": "..."}


class ToolResult(BaseModel):
    """Outcome of a successful action. Failures are raised as ToolError subclasses."""

    summary: str  # short, human-readable; shown in the live log and the report
    output: dict[str, Any] = Field(default_factory=dict)
    url: str | None = None  # link to the created item, for the report
    undo_handle: UndoHandle | None = None


class ActionSpec(BaseModel):
    """Describes one action a tool offers. The registry builds the planner's catalogue from these."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    params_model: type[BaseModel]  # validates and documents the action's parameters
    reversible: bool  # True means execute() must return an undo_handle


# --------------------------------------------------------------------------- adapter


def _describe_validation_error(exc: ValidationError) -> str:
    # Field names and messages only: the offending input may be user text we do not want
    # echoed into logs and error reports.
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or 'params'}: {e['msg']}"
        for e in exc.errors(include_input=False)
    )


class ToolAdapter(ABC):
    """Base class for a tool. Subclasses set `name` and `actions` and implement `_run`.

    `execute` is a template method: it validates, delegates to `_run`, then enforces the
    undo-handle rule, so no adapter can forget to say how its action is reversed.
    """

    name: ClassVar[str]
    actions: ClassVar[dict[str, ActionSpec]]

    async def execute(self, action: str, params: dict[str, Any]) -> ToolResult:
        spec = self.actions.get(action)
        if spec is None:
            available = ", ".join(sorted(self.actions)) or "none"
            raise FatalToolError(
                f"{self.name} has no action {action!r} (available: {available})"
            )
        try:
            validated = spec.params_model.model_validate(params)
        except ValidationError as exc:
            raise FatalToolError(
                f"Invalid parameters for {self.name}.{action}: "
                f"{_describe_validation_error(exc)}"
            ) from exc

        result = await self._run(action, validated)

        # The undo handle must be recorded before the next step runs (rollback depends on
        # it), so a mismatch with the declared reversibility is a bug we fail loudly on.
        if spec.reversible and result.undo_handle is None:
            raise ToolContractError(
                f"{self.name}.{action} is reversible but returned no undo handle"
            )
        if not spec.reversible and result.undo_handle is not None:
            raise ToolContractError(
                f"{self.name}.{action} is not reversible but returned an undo handle"
            )
        return result

    async def undo(self, handle: UndoHandle) -> None:
        if handle.tool != self.name:
            raise FatalToolError(
                f"Undo handle belongs to {handle.tool!r}, not {self.name!r}"
            )
        spec = self.actions.get(handle.action)
        if spec is None or not spec.reversible:
            raise FatalToolError(f"{self.name}.{handle.action} cannot be undone")
        await self._undo(handle)

    @abstractmethod
    async def _run(self, action: str, params: BaseModel) -> ToolResult:
        """Perform the action. `params` is already validated against the action's model."""

    async def _undo(self, handle: UndoHandle) -> None:
        """Reverse an action. Tools with reversible actions must override this."""
        raise FatalToolError(f"{self.name} does not implement undo")
