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
    "SLACK_WEBHOOK_URL": "https://hooks.example.invalid/slack-secret",
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


def test_llm_provider_defaults_to_google_with_its_default_model(
    full_env: dict[str, str],
) -> None:
    settings = load()
    assert settings.llm_provider == "google"
    assert settings.llm_model_name == "gemini-3.8-flash"


def test_switching_provider_switches_the_default_model(
    full_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert load().llm_model_name == "claude-sonnet-5-5"


def test_explicit_llm_model_wins_over_the_default(
    full_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_MODEL", "gemini-2.5-flash-lite")
    assert load().llm_model_name == "gemini-2.5-flash-lite"


def test_unknown_llm_provider_is_rejected(
    full_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(ValidationError) as exc_info:
        load()
    assert {error["loc"][0] for error in exc_info.value.errors()} == {"llm_provider"}


def test_missing_variable_is_reported_by_name(
    full_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NOTION_TOKEN")
    monkeypatch.delenv("NOTION_PAGE_ID")
    with pytest.raises(ValidationError) as exc_info:
        load()
    missing = {error["loc"][0] for error in exc_info.value.errors()}
    assert missing == {"notion_token", "notion_page_id"}


def test_secrets_never_appear_in_repr_or_str(full_env: dict[str, str]) -> None:
    settings = load()
    rendered = repr(settings) + str(settings)
    for name in (
        "LLM_API_KEY",
        "NOTION_TOKEN",
        "TEAMS_WEBHOOK_URL",
        "SLACK_WEBHOOK_URL",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
    ):
        assert FAKE_ENV[name] not in rendered


def test_notifier_defaults_to_teams(full_env: dict[str, str]) -> None:
    settings = load()
    assert settings.notifier_provider == "teams"
    assert settings.notifier_webhook_url is not None
    assert (
        settings.notifier_webhook_url.get_secret_value()
        == FAKE_ENV["TEAMS_WEBHOOK_URL"]
    )


def test_slack_provider_uses_slack_url_and_needs_no_teams_url(
    full_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOTIFIER_PROVIDER", "slack")
    monkeypatch.delenv("TEAMS_WEBHOOK_URL")
    settings = load()
    assert settings.notifier_webhook_url is not None
    assert (
        settings.notifier_webhook_url.get_secret_value()
        == FAKE_ENV["SLACK_WEBHOOK_URL"]
    )


@pytest.mark.parametrize(
    ("provider", "missing_variable"),
    [("slack", "SLACK_WEBHOOK_URL"), ("teams", "TEAMS_WEBHOOK_URL")],
)
def test_selected_notifier_url_is_required_and_named(
    full_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    missing_variable: str,
) -> None:
    monkeypatch.setenv("NOTIFIER_PROVIDER", provider)
    monkeypatch.delenv(missing_variable)
    with pytest.raises(ValidationError, match=missing_variable):
        load()


def test_empty_notifier_url_counts_as_missing(
    full_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOTIFIER_PROVIDER", "slack")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "")
    with pytest.raises(ValidationError, match="SLACK_WEBHOOK_URL"):
        load()


def test_unknown_notifier_provider_is_rejected(
    full_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOTIFIER_PROVIDER", "discord")
    with pytest.raises(ValidationError) as exc_info:
        load()
    assert {error["loc"][0] for error in exc_info.value.errors()} == {
        "notifier_provider"
    }


def test_env_example_matches_settings_fields() -> None:
    """Pass gate: .env.example must document every variable the app reads, and nothing else."""
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    documented = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", text, flags=re.MULTILINE))
    declared = {name.upper() for name in Settings.model_fields}
    assert documented == declared
