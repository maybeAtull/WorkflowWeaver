"""Typed application settings, loaded from environment variables / the repo-root .env file.

Design notes:
- Secrets are SecretStr so they print as '**********' in logs, reprs and tracebacks.
  Code must call .get_secret_value() on purpose, which keeps accidental leaks out.
- Nothing is validated at import time. Call get_settings() where configuration is needed;
  this keeps tests and tooling (e.g. the health check) free of credential requirements.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> repo root. Resolved from __file__ so it works no matter
# which directory uvicorn or pytest is launched from; docker-compose reads the same file.
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

LlmProvider = Literal["google", "anthropic"]
NotifierProvider = Literal["slack", "teams"]

# Used when LLM_MODEL is empty, so switching LLM_PROVIDER alone never pairs a provider
# with another provider's model id.
DEFAULT_LLM_MODELS: dict[LlmProvider, str] = {
    "google": "gemini-3.8-flash",
    "anthropic": "claude-sonnet-5-5",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",  # the shared .env may hold variables only docker-compose uses
    )

    # LLM: a single key variable; llm_provider says which provider that key belongs to.
    llm_provider: LlmProvider = "google"
    llm_api_key: SecretStr
    llm_model: str = ""  # empty -> provider default, see llm_model_name

    # Notion (MCP)
    notion_token: SecretStr
    notion_page_id: str

    # Google Drive (MCP)
    google_credentials_path: Path
    google_drive_folder_id: str

    # Notifications: the final workflow step posts to a chat tool via an incoming webhook.
    # Only the URL for the selected provider is required (enforced below). Teams is the
    # default because the assessment brief names it. A webhook URL grants posting rights,
    # so both are secrets.
    notifier_provider: NotifierProvider = "teams"
    slack_webhook_url: SecretStr | None = None
    teams_webhook_url: SecretStr | None = None

    # Langfuse
    langfuse_public_key: SecretStr
    langfuse_secret_key: SecretStr
    langfuse_host: str = "https://cloud.langfuse.com"

    # Failure injection for demoing retry/rollback deterministically (empty = disabled).
    simulate_failure_step: str = ""

    @property
    def notifier_webhook_url(self) -> SecretStr | None:
        """The webhook URL for the selected notifier provider (None if not set)."""
        return (
            self.slack_webhook_url
            if self.notifier_provider == "slack"
            else self.teams_webhook_url
        )

    @model_validator(mode="after")
    def _require_selected_notifier_webhook(self) -> "Settings":
        # An empty value (e.g. a bare `SLACK_WEBHOOK_URL=` copied from a template) counts as missing.
        url = self.notifier_webhook_url
        if url is None or not url.get_secret_value().strip():
            variable = f"{self.notifier_provider.upper()}_WEBHOOK_URL"
            raise ValueError(
                f"{variable} is required when NOTIFIER_PROVIDER={self.notifier_provider}"
            )
        return self

    @property
    def llm_model_name(self) -> str:
        """The model id to use: the explicit LLM_MODEL, else the provider's default."""
        return self.llm_model or DEFAULT_LLM_MODELS[self.llm_provider]


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, validating them on first use."""
    return Settings()
