"""Tool registry: one place to ask "what can you do?", "is this step valid?" and "do it / undo it".

- The planner reads `render_catalogue()`: a compact text list of the actions it may use.
- The plan validator calls `validate_step()` and gets plain problems it can repair.
- The executor calls `execute()` and `undo()`; an undo handle names its tool, so rollback
  needs no lookup logic of its own.
"""

from collections.abc import Iterable, Mapping
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.tools.base import (
    ActionSpec,
    FatalToolError,
    ToolAdapter,
    ToolResult,
    UndoHandle,
    VerifyResult,
)
from app.tools.drive import DriveAdapter
from app.tools.notifier import get_notifier
from app.tools.notion import McpCaller, NotionAdapter


def _object_text(definition: dict[str, Any]) -> str:
    return "{" + ", ".join((definition.get("properties") or {}).keys()) + "}"


def _type_text(schema: dict[str, Any], defs: dict[str, Any]) -> str:
    """Render a JSON-schema type in a few words (the catalogue goes into the LLM prompt)."""
    if "$ref" in schema:
        return _object_text(defs[schema["$ref"].split("/")[-1]])
    if "anyOf" in schema:  # Optional[X] appears as anyOf [X, null]
        parts = [
            _type_text(s, defs) for s in schema["anyOf"] if s.get("type") != "null"
        ]
        return " or ".join(parts)
    if "enum" in schema:
        return "|".join(str(value) for value in schema["enum"])
    kind = schema.get("type")
    if kind == "string":
        limit = schema.get("maxLength")
        return f"text \N{LESS-THAN OR EQUAL TO}{limit}" if limit else "text"
    if kind == "array":
        items = _type_text(schema.get("items", {}), defs)
        limit = schema.get("maxItems")
        return f"list of {items}" + (
            f" (\N{LESS-THAN OR EQUAL TO}{limit})" if limit else ""
        )
    if kind == "object":
        return _object_text(schema)
    return str(kind or "any")


def render_params(model: type[BaseModel], hidden: frozenset[str]) -> str:
    """`title*: text <=200, content: text <=10000, ...`  (* marks a required parameter)."""
    schema = model.model_json_schema()
    defs = schema.get("$defs", {})
    required = set(schema.get("required", []))
    parts = []
    for name, prop in (schema.get("properties") or {}).items():
        if name in hidden:
            continue
        mark = "*" if name in required else ""
        parts.append(f"{name}{mark}: {_type_text(prop, defs)}")
    return ", ".join(parts)


class ToolRegistry:
    def __init__(self, adapters: Iterable[ToolAdapter]) -> None:
        self._adapters: dict[str, ToolAdapter] = {}
        for adapter in adapters:
            if adapter.name in self._adapters:
                raise ValueError(f"Duplicate tool name {adapter.name!r}")
            self._adapters[adapter.name] = adapter

    # ------------------------------------------------------------------ lookup

    def adapter(self, tool: str) -> ToolAdapter:
        found = self._adapters.get(tool)
        if found is None:
            available = ", ".join(sorted(self._adapters))
            raise FatalToolError(f"Unknown tool {tool!r} (available: {available})")
        return found

    def spec(self, tool: str, action: str) -> ActionSpec:
        found = self.adapter(tool).actions.get(action)
        if found is None:
            raise FatalToolError(f"{tool} has no action {action!r}")
        return found

    def _visible_actions(self, tool: str) -> dict[str, ActionSpec]:
        return {
            n: s for n, s in self._adapters[tool].actions.items() if s.planner_visible
        }

    # ------------------------------------------------------------------ for the planner

    def render_catalogue(self) -> str:
        """Compact text of what the planner may use, one line per action."""
        lines = []
        for tool in sorted(self._adapters):
            for name, spec in sorted(self._visible_actions(tool).items()):
                params = render_params(spec.params_model, spec.planner_hidden_params)
                lines.append(f"{tool}.{name}({params}) - {spec.description}")
        return "\n".join(lines)

    def must_be_last(self, tool: str, action: str) -> bool:
        """True for actions that cannot be undone (the notification): they must end the plan."""
        spec = self.spec(tool, action)
        return spec.planner_visible and not spec.reversible

    def validate_step(
        self, tool: str, action: str, params: Mapping[str, Any]
    ) -> list[str]:
        """Plain-language problems with a planned step (empty list means valid)."""
        if tool not in self._adapters:
            return [
                f"unknown tool '{tool}' (available: {', '.join(sorted(self._adapters))})"
            ]
        visible = self._visible_actions(tool)
        if action not in visible:
            return [
                f"unknown action '{tool}.{action}' (available: {', '.join(sorted(visible))})"
            ]

        spec = visible[action]
        problems: list[str] = []
        allowed = set(spec.params_model.model_fields) - spec.planner_hidden_params
        unknown = sorted(set(params) - allowed)
        if unknown:
            # The adapters' own models would silently ignore extras, dropping whatever the
            # LLM invented, so the registry is stricter on purpose.
            problems.append(
                f"{tool}.{action}: unknown parameter(s) {unknown} (allowed: {sorted(allowed)})"
            )
        try:
            spec.params_model.model_validate(dict(params))
        except ValidationError as exc:
            # Field names and messages only: the offending value may be user text.
            for error in exc.errors(include_input=False):
                where = ".".join(str(part) for part in error["loc"]) or "params"
                problems.append(f"{tool}.{action}.{where}: {error['msg']}")
        return problems

    # ------------------------------------------------------------------ for the executor

    async def execute(
        self, tool: str, action: str, params: Mapping[str, Any]
    ) -> ToolResult:
        """Run any action, including the verification-only ones the planner never sees."""
        self.spec(tool, action)  # a clear error for an unknown tool or action
        return await self.adapter(tool).execute(action, dict(params))

    async def undo(self, handle: UndoHandle) -> None:
        await self.adapter(handle.tool).undo(handle)

    async def verify(self, handle: UndoHandle) -> VerifyResult:
        """Read a created item back from its service (the handle names its tool)."""
        return await self.adapter(handle.tool).verify(handle)


def build_registry(
    settings: Settings, mcp: McpCaller, http: httpx.AsyncClient | None = None
) -> ToolRegistry:
    """The three tools wired to this run's MCP session manager and to the settings.

    Building it starts nothing: MCP servers start lazily, on a tool's first call.
    """
    return ToolRegistry(
        [
            NotionAdapter(mcp, settings.notion_page_id),
            DriveAdapter(mcp, settings.google_drive_folder_id),
            get_notifier(settings, http),
        ]
    )
