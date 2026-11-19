"""Workflow templates: ready-made requests a business user fills in instead of writing from scratch.

A template is a JSON file in this folder. Its `request_template` is plain English with
`{{key}}` placeholders; the user's field values are put into it and the result is the request
the agent runs, exactly as if the user had typed it. Nothing about the agent changes.

Templates are validated when they are loaded (at startup), so a broken file fails loudly and
early instead of the first time someone picks it.
"""

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.agent.nodes.parse_intent import MAX_REQUEST_CHARS

TEMPLATES_DIR = Path(__file__).parent
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")

FieldKind = Literal["text", "longtext", "choice"]


class TemplateError(ValueError):
    """A problem with what the user entered. The message is plain language, safe to show."""


class TemplateNotFound(LookupError):
    pass


class TemplateField(BaseModel):
    key: str = Field(pattern=r"^\w+$")
    label: str
    help: str = ""
    kind: FieldKind = "text"
    required: bool = True
    max_length: int = Field(default=200, ge=1)
    options: list[str] = Field(default_factory=list)  # for kind "choice"

    @model_validator(mode="after")
    def _choice_needs_options(self) -> "TemplateField":
        if (self.kind == "choice") != bool(self.options):
            raise ValueError(f"field {self.key!r}: only choice fields have options")
        return self


class Template(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    order: int
    name: str
    description: str
    fields: list[TemplateField]
    request_template: str
    example: dict[str, str]

    @model_validator(mode="after")
    def _is_consistent(self) -> "Template":
        keys = [f.key for f in self.fields]
        if len(set(keys)) != len(keys):
            raise ValueError(f"template {self.id!r}: duplicate field keys")
        used = set(PLACEHOLDER.findall(self.request_template))
        if used != set(keys):
            raise ValueError(
                f"template {self.id!r}: placeholders {sorted(used)} do not match "
                f"fields {sorted(keys)}"
            )
        # Even with every field at its maximum, the request must fit what the agent accepts.
        worst_case = len(self.request_template) + sum(f.max_length for f in self.fields)
        if worst_case > MAX_REQUEST_CHARS:
            raise ValueError(
                f"template {self.id!r} can exceed {MAX_REQUEST_CHARS} characters"
            )
        try:
            self.render(self.example)
        except TemplateError as error:
            raise ValueError(f"template {self.id!r}: bad example: {error}") from None
        return self

    def render(self, values: dict[str, str]) -> str:
        """The request text for these field values. Raises TemplateError in plain language."""
        by_key = {f.key: f for f in self.fields}
        unknown = sorted(set(values) - set(by_key))
        if unknown:
            raise TemplateError(f"This template has no field called {unknown[0]!r}.")
        cleaned: dict[str, str] = {}
        for field in self.fields:
            value = values.get(field.key, "").strip()
            if not value and field.required:
                raise TemplateError(f"Please fill in “{field.label}”.")
            if len(value) > field.max_length:
                raise TemplateError(
                    f"“{field.label}” is too long: {len(value):,} characters, "
                    f"the limit is {field.max_length:,}."
                )
            if value and field.kind == "choice" and value not in field.options:
                raise TemplateError(
                    f"“{field.label}” must be one of: {', '.join(field.options)}."
                )
            cleaned[field.key] = value
        # One pass over the template: a value that itself contains "{{x}}" is never expanded.
        return PLACEHOLDER.sub(lambda m: cleaned[m.group(1)], self.request_template)


def load_templates(directory: Path = TEMPLATES_DIR) -> list[Template]:
    """Every template in the folder, in display order. Raises on a broken or duplicate one."""
    templates = [
        Template.model_validate(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(directory.glob("*.json"))
    ]
    ids = [t.id for t in templates]
    if len(set(ids)) != len(ids):
        raise ValueError("Two templates share the same id.")
    return sorted(templates, key=lambda t: (t.order, t.id))
