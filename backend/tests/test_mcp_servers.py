from pathlib import Path

import pytest

from app.config import Settings
from app.tools.mcp_servers import (
    DRIVE_ALLOWED_TOOLS,
    DRIVE_IMAGE,
    NOTION_ALLOWED_TOOLS,
    NOTION_IMAGE,
    default_specs,
    drive_spec,
    notion_spec,
)
from tests.test_config import FAKE_ENV


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(tmp_path / "creds.json"))
    monkeypatch.setenv("GOOGLE_TOKEN_DIR", str(tmp_path / "tokens"))
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_notion_token_is_passed_by_environment_never_on_the_command_line(
    settings: Settings,
) -> None:
    spec = notion_spec(settings)
    token = FAKE_ENV["NOTION_TOKEN"]
    assert token not in " ".join(spec.args)
    assert spec.env == {"NOTION_TOKEN": token}
    assert token in spec.secrets  # so it is scrubbed from error messages
    assert spec.args[-1] == NOTION_IMAGE


def test_drive_mounts_credentials_read_only_and_the_token_directory_writable(
    settings: Settings, tmp_path: Path
) -> None:
    args = drive_spec(settings).args
    assert (
        f"{(tmp_path / 'creds.json').as_posix()}:/config/gcp-oauth.keys.json:ro" in args
    )
    assert f"{(tmp_path / 'tokens').as_posix()}:/config/tokens" in args
    assert args[-1] == DRIVE_IMAGE


def test_drive_scopes_are_narrowed_and_no_secret_is_on_the_command_line(
    settings: Settings,
) -> None:
    spec = drive_spec(settings)
    assert spec.env == {"GOOGLE_DRIVE_MCP_SCOPES": "drive,documents"}
    assert FAKE_ENV["NOTION_TOKEN"] not in " ".join(spec.args)


def test_whitelists_contain_exactly_the_tools_the_adapters_need() -> None:
    assert NOTION_ALLOWED_TOOLS == {
        "API-post-page",
        "API-patch-page",
        "API-retrieve-a-page",
    }
    assert DRIVE_ALLOWED_TOOLS == {"listFolder", "createTextFile", "deleteItem"}


def test_default_specs_cover_both_servers(settings: Settings) -> None:
    assert [spec.name for spec in default_specs(settings)] == ["notion", "drive"]
