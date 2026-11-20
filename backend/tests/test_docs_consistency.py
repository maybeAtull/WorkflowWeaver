"""The settings, `.env.example` and the README's variable table must list the same variables.

Without this, adding a setting and forgetting the docs (a pass gate of the assessment) would only
be noticed by a reader. It reads the three sources as plain text, so it needs no credentials.
"""

import re
from pathlib import Path

from app.config import ENV_FILE, Settings

ROOT = ENV_FILE.parent


def settings_variables() -> set[str]:
    return {name.upper() for name in Settings.model_fields}


def example_variables() -> set[str]:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    return set(re.findall(r"^([A-Z][A-Z0-9_]*)=", text, flags=re.MULTILINE))


def readme_variables() -> set[str]:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    section = text.split("## 4. Environment Variables", 1)[1].split("## 5.", 1)[0]
    return set(re.findall(r"^\| `([A-Z][A-Z0-9_]*)` \|", section, flags=re.MULTILINE))


def test_env_example_lists_every_setting() -> None:
    assert example_variables() == settings_variables()


def test_readme_table_lists_every_setting() -> None:
    assert readme_variables() == settings_variables()


def test_env_example_defaults_match_the_brief() -> None:
    values = dict(
        re.findall(
            r"^([A-Z][A-Z0-9_]*)=(.*)$",
            (ROOT / ".env.example").read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
    )
    assert values["NOTIFIER_PROVIDER"] == "teams"  # the brief names Teams
    assert values["LLM_PROVIDER"] == "google"


def test_the_readme_has_the_eight_required_sections() -> None:
    text = Path(ROOT / "README.md").read_text(encoding="utf-8")
    for title in (
        "Project Overview",
        "Architecture Description",
        "Setup Instructions",
        "Environment Variables",
        "Frontend Run Instructions",
        "Backend Run Instructions",
        "Sample Workflow Examples",
        "Known Limitations",
    ):
        assert re.search(rf"^## \d\. {title}$", text, flags=re.MULTILINE), title
    assert (ROOT / "docs" / "architecture.png").is_file()
