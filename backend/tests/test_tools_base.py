from typing import ClassVar

import pytest
from pydantic import BaseModel

from app.tools.base import (
    ActionSpec,
    FatalToolError,
    ToolAdapter,
    ToolContractError,
    ToolError,
    ToolResult,
    TransientToolError,
    UndoHandle,
    is_safe_to_retry,
)


class TitleParams(BaseModel):
    title: str


def _spec(name: str, *, reversible: bool) -> ActionSpec:
    return ActionSpec(
        name=name,
        description=f"test action {name}",
        params_model=TitleParams,
        reversible=reversible,
    )


class FakeTool(ToolAdapter):
    """In-memory adapter used only to exercise the base-class rules."""

    name = "fake"
    actions: ClassVar[dict[str, ActionSpec]] = {
        "create": _spec("create", reversible=True),
        "announce": _spec("announce", reversible=False),
        "create_without_handle": _spec("create_without_handle", reversible=True),
        "announce_with_handle": _spec("announce_with_handle", reversible=False),
        "flaky": _spec("flaky", reversible=False),
    }

    def __init__(self) -> None:
        self.undone: list[UndoHandle] = []

    def _handle(self, action: str) -> UndoHandle:
        return UndoHandle(tool="fake", action=action, data={"id": "item-1"})

    async def _run(self, action: str, params: BaseModel) -> ToolResult:
        assert isinstance(params, TitleParams)  # arrives already validated
        if action == "create":
            return ToolResult(
                summary=f"created {params.title}", undo_handle=self._handle(action)
            )
        if action == "announce":
            return ToolResult(summary=f"posted {params.title}")
        if action == "create_without_handle":
            return ToolResult(summary="forgot the handle")
        if action == "announce_with_handle":
            return ToolResult(
                summary="unexpected handle", undo_handle=self._handle(action)
            )
        raise TransientToolError("503 service unavailable")

    async def _undo(self, handle: UndoHandle) -> None:
        self.undone.append(handle)


async def test_execute_validates_params_and_returns_the_undo_handle() -> None:
    result = await FakeTool().execute("create", {"title": "Plan"})
    assert result.summary == "created Plan"
    assert result.undo_handle == UndoHandle(
        tool="fake", action="create", data={"id": "item-1"}
    )


async def test_non_reversible_action_needs_no_undo_handle() -> None:
    result = await FakeTool().execute("announce", {"title": "Done"})
    assert result.undo_handle is None


async def test_unknown_action_is_fatal_and_lists_the_available_ones() -> None:
    with pytest.raises(
        FatalToolError, match="no action 'explode'.*available: .*create"
    ):
        await FakeTool().execute("explode", {})


async def test_missing_param_is_fatal_and_names_the_field() -> None:
    with pytest.raises(FatalToolError, match="fake.create.*title"):
        await FakeTool().execute("create", {})


async def test_invalid_param_error_does_not_echo_the_input_value() -> None:
    with pytest.raises(FatalToolError) as exc_info:
        await FakeTool().execute("create", {"title": ["SECRET-USER-TEXT"]})
    assert "SECRET-USER-TEXT" not in str(exc_info.value)


async def test_reversible_action_without_undo_handle_is_a_contract_error() -> None:
    with pytest.raises(ToolContractError, match="returned no undo handle"):
        await FakeTool().execute("create_without_handle", {"title": "x"})


async def test_non_reversible_action_with_undo_handle_is_a_contract_error() -> None:
    with pytest.raises(ToolContractError, match="not reversible but returned"):
        await FakeTool().execute("announce_with_handle", {"title": "x"})


async def test_transient_errors_propagate_and_are_distinct_from_fatal_ones() -> None:
    with pytest.raises(TransientToolError):
        await FakeTool().execute("flaky", {"title": "x"})
    assert not issubclass(TransientToolError, FatalToolError)
    assert issubclass(FatalToolError, ToolError)
    assert issubclass(ToolContractError, FatalToolError)


async def test_undo_runs_the_adapters_undo_with_the_handle() -> None:
    tool = FakeTool()
    result = await tool.execute("create", {"title": "Plan"})
    assert result.undo_handle is not None
    await tool.undo(result.undo_handle)
    assert tool.undone == [result.undo_handle]


async def test_undo_rejects_a_handle_from_another_tool() -> None:
    other = UndoHandle(tool="other", action="create")
    with pytest.raises(FatalToolError, match="belongs to 'other'"):
        await FakeTool().undo(other)


async def test_undo_rejects_a_non_reversible_action() -> None:
    handle = UndoHandle(tool="fake", action="announce")
    with pytest.raises(FatalToolError, match="cannot be undone"):
        await FakeTool().undo(handle)


def test_transient_errors_assume_an_unknown_outcome_unless_told_otherwise() -> None:
    assert TransientToolError("x").outcome_unknown is True
    assert TransientToolError("x", outcome_unknown=False).outcome_unknown is False


def test_actions_are_not_repeat_safe_by_default() -> None:
    """Forgetting the flag must fail safe, towards not retrying."""
    assert _spec("x", reversible=False).repeat_safe is False


@pytest.mark.parametrize(
    ("outcome_unknown", "repeat_safe", "expected"),
    [
        (False, False, True),  # provably did not run: retry
        (False, True, True),
        (True, True, True),  # may have run, but repeating is harmless: retry
        (
            True,
            False,
            False,
        ),  # may have run and repeating could duplicate: do not retry
    ],
)
def test_the_retry_rule(
    outcome_unknown: bool, repeat_safe: bool, expected: bool
) -> None:
    spec = ActionSpec(
        name="a",
        description="d",
        params_model=TitleParams,
        reversible=False,
        repeat_safe=repeat_safe,
    )
    error = TransientToolError("x", outcome_unknown=outcome_unknown)
    assert is_safe_to_retry(error, spec) is expected


def test_undo_handle_survives_a_json_round_trip() -> None:
    """It is stored in the agent state and in SQLite, so it must serialize losslessly."""
    handle = UndoHandle(tool="notion", action="create_page", data={"page_id": "abc"})
    assert UndoHandle.model_validate_json(handle.model_dump_json()) == handle
