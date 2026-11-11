"""Typed application settings, loaded from environment variables / the repo-root .env file.

Design notes:
- Secrets are SecretStr so they print as '**********' in logs, reprs and tracebacks.
  Code must call .get_secret_value() on purpose, which keeps accidental leaks out.
- Nothing is validated at import time. Call get_settings() where configuration is needed;
  this keeps tests and tooling (e.g. the health check) free of credential requirements.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> repo root. Resolved from __file__ so it works no matter
# which directory uvicorn or pytest is launched from; docker-compose reads the same file.
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",  # the shared .env may hold variables only docker-compose uses
    )

    # LLM
    llm_api_key: SecretStr
    llm_model: str = "claude-sonnet-5-5"

    # Notion (MCP)
    notion_token: SecretStr
    notion_page_id: str

    # Google Drive (MCP)
    google_credentials_path: Path
    google_drive_folder_id: str

    # Microsoft Teams: the webhook URL itself grants posting rights, so treat it as a secret.
    teams_webhook_url: SecretStr

    # Langfuse
    langfuse_public_key: SecretStr
    langfuse_secret_key: SecretStr
    langfuse_host: str = "https://cloud.langfuse.com"

    # Failure injection for demoing retry/rollback deterministically (empty = disabled).
    simulate_failure_step: str = ""


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, validating them on first use."""
    return Settings()
