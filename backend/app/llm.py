"""The single place that knows which LLM provider is in use.

Everything else (agent nodes, scripts) calls get_llm() and works with the provider-neutral
LangChain chat-model interface, so switching providers is a .env change, not a code change.
Provider packages are imported lazily so only the selected one has to be importable.
"""

from langchain_core.language_models.chat_models import BaseChatModel

from app.config import Settings, get_settings

# Bound every call so a provider outage fails fast instead of stalling a workflow. The
# libraries' own defaults hid long waits (Google retries 6 times: one call took 3 minutes).
# Retries are kept low here on purpose: the agent's own retry-with-backoff layer (Phase 2)
# does the real retrying, and it reports each attempt to the user.
LLM_TIMEOUT_SECONDS = 60
LLM_MAX_RETRIES = 2


def get_llm(settings: Settings | None = None) -> BaseChatModel:
    """Build the chat model for the configured provider."""
    settings = settings or get_settings()
    api_key = settings.llm_api_key.get_secret_value()
    model = settings.llm_model_name

    if settings.llm_provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=model,
            google_api_key=api_key,
            timeout=LLM_TIMEOUT_SECONDS,
            max_retries=LLM_MAX_RETRIES,
        )

    if settings.llm_provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model,
            api_key=api_key,
            timeout=LLM_TIMEOUT_SECONDS,
            max_retries=LLM_MAX_RETRIES,
        )

    if settings.llm_provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(
            model=model,
            api_key=api_key,
            timeout=LLM_TIMEOUT_SECONDS,
            max_retries=LLM_MAX_RETRIES,
        )

    # Unreachable while LlmProvider is validated, but fails loudly if a provider is added
    # to the config without being wired up here.
    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider!r}")
