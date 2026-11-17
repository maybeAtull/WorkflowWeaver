"""Smoke test for Langfuse tracing.

Run from the backend/ folder:  python -m scripts.smoke_langfuse

Checks the keys, emits ONE synthetic trace shaped like a real run (root run > node span >
LLM generation + tool call), flushes it, then reads the observations back through the API to
confirm Langfuse received them. Never prints the keys. Exits non-zero on failure.
"""

import sys
import time
import uuid

from langfuse import Langfuse
from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import ENV_FILE

# Ingestion is asynchronous, so the trace can take a while to become readable.
VERIFY_TIMEOUT_SECONDS = 60
VERIFY_POLL_SECONDS = 3
EXPECTED_OBSERVATIONS = {"smoke-test-run", "node:plan", "llm-call", "tool:notifier"}


class LangfuseSettings(BaseSettings):
    """Only what this script needs, so a Langfuse test doesn't demand every other credential."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )
    langfuse_public_key: SecretStr
    langfuse_secret_key: SecretStr
    langfuse_host: str = "https://cloud.langfuse.com"


def report(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    return ok


def emit_trace(client: Langfuse, run_id: str) -> str:
    """Create a nested synthetic trace and return its trace id."""
    with client.start_as_current_observation(
        name="smoke-test-run",
        as_type="span",
        input={"workflow": "smoke test"},
        metadata={"run_id": run_id},
    ) as root:
        trace_id = client.get_current_trace_id()
        with client.start_as_current_observation(name="node:plan", as_type="span"):
            with client.start_as_current_observation(
                name="llm-call",
                as_type="generation",
                model="smoke-model",
                input="synthetic prompt",
                usage_details={"input": 12, "output": 8},
            ) as generation:
                generation.update(output="synthetic reply")
            with client.start_as_current_observation(
                name="tool:notifier", as_type="tool", input={"message": "synthetic"}
            ) as tool:
                tool.update(output={"ok": True})
        root.update(output={"status": "ok"})
    if trace_id is None:
        raise RuntimeError("Langfuse did not return a trace id")
    return trace_id


def observed_names(client: Langfuse, trace_id: str) -> set[str]:
    names: set[str] = set()
    for page in client.iter_observation_pages(trace_id=trace_id, fields="basic"):
        names.update(observation.name for observation in page.data if observation.name)
    return names


def verify_received(client: Langfuse, trace_id: str) -> tuple[bool, set[str]]:
    deadline = time.monotonic() + VERIFY_TIMEOUT_SECONDS
    names: set[str] = set()
    while time.monotonic() < deadline:
        names = observed_names(client, trace_id)
        if EXPECTED_OBSERVATIONS <= names:
            return True, names
        time.sleep(VERIFY_POLL_SECONDS)
    return False, names


def run(settings: LangfuseSettings) -> bool:
    client = Langfuse(
        public_key=settings.langfuse_public_key.get_secret_value(),
        secret_key=settings.langfuse_secret_key.get_secret_value(),
        base_url=settings.langfuse_host,
        timeout=20,
    )
    try:
        try:
            authenticated = client.auth_check()
        # Any failure here means authentication did not work, so catching broadly is intended.
        except Exception as exc:  # noqa: BLE001
            return report(
                "authenticate", False, f"{type(exc).__name__}: {str(exc)[:200]}"
            )
        if not authenticated:
            return report(
                "authenticate",
                False,
                "keys rejected; check both keys and that LANGFUSE_HOST matches your region "
                "(EU: https://cloud.langfuse.com, US: https://us.cloud.langfuse.com)",
            )
        report("authenticate", True, f"host={settings.langfuse_host}")

        run_id = uuid.uuid4().hex[:8]
        trace_id = emit_trace(client, run_id)
        client.flush()
        report("emit trace", True, f"trace_id={trace_id}")

        received, names = verify_received(client, trace_id)
        url = client.get_trace_url(trace_id=trace_id)
        return report(
            "trace received by Langfuse",
            received,
            f"observations={sorted(names)}" + (f", view: {url}" if url else ""),
        )
    finally:
        client.shutdown()


def main() -> int:
    try:
        settings = LangfuseSettings()
    except ValidationError as exc:
        # Field names and messages only; never the offending input values.
        problems = [
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        ]
        report("config loads", False, "; ".join(problems))
        return 1
    report("config loads", True, "Langfuse keys are set")
    return 0 if run(settings) else 1


if __name__ == "__main__":
    sys.exit(main())
