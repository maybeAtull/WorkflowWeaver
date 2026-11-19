"""The template files and the rendering of a request from field values."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.agent.nodes.parse_intent import MAX_REQUEST_CHARS
from app.templates import Template, TemplateError, load_templates


def sample(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "demo",
        "order": 1,
        "name": "Demo",
        "description": "A demo.",
        "fields": [
            {"key": "title", "label": "Title", "max_length": 20},
            {"key": "body", "label": "Body", "kind": "longtext", "max_length": 100},
        ],
        "request_template": 'Make "{{title}}".\n{{body}}',
        "example": {"title": "T", "body": "B"},
    }
    data.update(overrides)
    return data


def build(**overrides: Any) -> Template:
    return Template.model_validate(sample(**overrides))


# ------------------------------------------------------------------ the shipped files


def test_the_three_shipped_templates_load_in_display_order() -> None:
    templates = load_templates()
    assert [t.id for t in templates] == [
        "meeting_to_tasks",
        "sprint_planning",
        "incident_response",
    ]


@pytest.mark.parametrize("template", load_templates(), ids=lambda t: t.id)
def test_every_example_renders_and_names_every_destination(template: Template) -> None:
    text = template.render(template.example)
    assert 0 < len(text) <= MAX_REQUEST_CHARS
    assert "{{" not in text
    # The agent plans from this text, so it must ask for all three tools.
    assert "Notion" in text and "Google Drive" in text and "chat" in text


@pytest.mark.parametrize("template", load_templates(), ids=lambda t: t.id)
def test_the_worst_case_request_still_fits_the_agents_limit(template: Template) -> None:
    longest = {f.key: "x" * f.max_length for f in template.fields}
    for field in template.fields:
        if field.kind == "choice":
            longest[field.key] = field.options[0]
    assert len(template.render(longest)) <= MAX_REQUEST_CHARS


def test_the_examples_are_synthetic_markers_not_real_data() -> None:
    # A light guard: the fictional framing must stay visible in the example text.
    blob = " ".join(
        value for t in load_templates() for value in t.example.values()
    ).lower()
    assert "falcon" in blob and "fictional" in blob


# ------------------------------------------------------------------ rendering


def test_values_are_put_into_the_request() -> None:
    assert build().render({"title": "Hi", "body": "Notes"}) == 'Make "Hi".\nNotes'


def test_values_are_trimmed() -> None:
    assert (
        build().render({"title": "  Hi  ", "body": "\nNotes\n"}) == 'Make "Hi".\nNotes'
    )


def test_a_value_that_looks_like_a_placeholder_is_not_expanded() -> None:
    text = build().render({"title": "{{body}}", "body": "secret"})
    assert text == 'Make "{{body}}".\nsecret'


def test_a_missing_or_blank_required_field_names_the_field() -> None:
    with pytest.raises(TemplateError, match="Please fill in “Title”"):
        build().render({"body": "x"})
    with pytest.raises(TemplateError, match="Please fill in “Body”"):
        build().render({"title": "x", "body": "   "})


def test_an_optional_field_may_be_left_empty() -> None:
    data = sample()
    data["fields"][1]["required"] = False
    assert Template.model_validate(data).render({"title": "Hi"}) == 'Make "Hi".\n'


def test_an_unknown_key_and_a_too_long_value_are_refused_in_plain_words() -> None:
    with pytest.raises(TemplateError, match="no field called 'extra'"):
        build().render({"title": "a", "body": "b", "extra": "c"})
    with pytest.raises(TemplateError, match="too long: 21 characters, the limit is 20"):
        build().render({"title": "x" * 21, "body": "b"})


def test_a_choice_field_only_accepts_its_options() -> None:
    data = sample(
        fields=[
            {
                "key": "level",
                "label": "Level",
                "kind": "choice",
                "options": ["Low", "High"],
                "max_length": 10,
            }
        ],
        request_template="Level {{level}}",
        example={"level": "Low"},
    )
    template = Template.model_validate(data)
    assert template.render({"level": "High"}) == "Level High"
    with pytest.raises(TemplateError, match="must be one of: Low, High"):
        template.render({"level": "Medium"})


# ------------------------------------------------------------------ a broken template is refused


def test_placeholders_must_match_the_declared_fields() -> None:
    with pytest.raises(ValidationError, match="do not match"):
        build(request_template="Only {{title}}")
    with pytest.raises(ValidationError, match="do not match"):
        build(request_template="{{title}} {{body}} {{ghost}}")


def test_duplicate_field_keys_are_refused() -> None:
    fields = [{"key": "a", "label": "A"}, {"key": "a", "label": "A again"}]
    with pytest.raises(ValidationError, match="duplicate field keys"):
        build(fields=fields, request_template="{{a}}", example={"a": "x"})


def test_a_template_that_could_exceed_the_request_limit_is_refused() -> None:
    fields = [{"key": "a", "label": "A", "max_length": MAX_REQUEST_CHARS + 1}]
    with pytest.raises(ValidationError, match="can exceed"):
        build(fields=fields, request_template="{{a}}", example={"a": "x"})


def test_a_bad_example_is_refused() -> None:
    with pytest.raises(ValidationError, match="bad example"):
        build(example={"title": "T"})  # the required body is missing


def test_options_belong_to_choice_fields_only() -> None:
    fields = [{"key": "a", "label": "A", "options": ["x"]}]
    with pytest.raises(ValidationError, match="only choice fields have options"):
        build(fields=fields, request_template="{{a}}", example={"a": "x"})


def test_loading_rejects_a_broken_file_and_duplicate_ids(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text(json.dumps(sample()))
    (tmp_path / "b.json").write_text(json.dumps(sample(order=2)))
    with pytest.raises(ValueError, match="share the same id"):
        load_templates(tmp_path)
    (tmp_path / "b.json").write_text("{not json")
    with pytest.raises(ValueError):
        load_templates(tmp_path)
