import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]

# Obviously fake values; never use real credentials in tests.
FAKE_ENV = {
    "LLM_API_KEY": "sk-test-llm-secret",
    "NOTION_TOKEN": "ntn-test-notion-secret",
    "NOTION_PAGE_ID": "page-123",
    "GOOGLE_CREDENTIALS_PATH": "./credentials.json",
    "GOOGLE_DRIVE_FOLDER_ID": "folder-123",
    "TEAMS_WEBHOOK_URL": "https://example.invalid/webhook-secret",
    "LANGFUSE_PUBLIC_KEY": "pk-lf-test-public",
    "LANGFUSE_SECRET_KEY": "sk-lf-test-secret",
}


@pytest.fixture
def full_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    return FAKE_ENV


def load() -> Settings:
    # _env_file=None isolates the test from any real .env on the developer's machine.
    return Settings(_env_file=None)


def test_loads_values_and_applies_defaults(full_env: dict[str, str]) -> None:
    settings = load()
    assert settings.notion_page_id == "page-123"
    assert settings.llm_api_key.get_secret_value() == "sk-test-llm-secret"
    assert settings.langfuse_host == "https://cloud.langfuse.com"
    assert settings.simulate_failure_step == ""


def test_missing_variable_is_reported_by_name(
    full_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NOTION_TOKEN")
    monkeypatch.delenv("TEAMS_WEBHOOK_URL")
    with pytest.raises(ValidationError) as exc_info:
        load()
    missing = {error["loc"][0] for error in exc_info.value.errors()}
    assert missing == {"notion_token", "teams_webhook_url"}


def test_secrets_never_appear_in_repr_or_str(full_env: dict[str, str]) -> None:
    settings = load()
    rendered = repr(settings) + str(settings)
    for name in (
        "LLM_API_KEY",
        "NOTION_TOKEN",
        "TEAMS_WEBHOOK_URL",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
    ):
        assert FAKE_ENV[name] not in rendered


def test_env_example_matches_settings_fields() -> None:
    """Pass gate: .env.example must document every variable the app reads, and nothing else."""
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    documented = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", text, flags=re.MULTILINE))
    declared = {name.upper() for name in Settings.model_fields}
    assert documented == declared
