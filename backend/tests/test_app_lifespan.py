"""Startup and shutdown of the API: shared services are created once and the tracer is flushed."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import AppState
from app.config import Settings
from app.main import create_app
from app.observability.tracing import InMemoryTracer
from tests.test_config import FAKE_ENV


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        llm_provider="groq",
        notifier_provider="slack",
        database_path=tmp_path / "test.db",
    )


def test_health_works_without_startup() -> None:
    """A bare client (no `with`) never runs the lifespan, and health must still answer."""
    assert TestClient(create_app()).get("/api/health").json() == {"status": "ok"}


def test_startup_creates_the_shared_services_and_health_names_the_providers(
    settings: Settings,
) -> None:
    app = create_app(settings, InMemoryTracer())
    with TestClient(app) as client:
        state: AppState = app.state.services
        assert state.settings is settings
        assert client.get("/api/health").json() == {
            "status": "ok",
            "llm_provider": "groq",
            "notifier_provider": "slack",
            "database": "ok",
        }
        assert settings.database_file.exists()


def test_health_never_contains_a_secret(settings: Settings) -> None:
    with TestClient(create_app(settings, InMemoryTracer())) as client:
        body = client.get("/api/health").text
    assert not any(value in body for value in FAKE_ENV.values())


def test_shutdown_shuts_the_tracer_down_exactly_once(settings: Settings) -> None:
    tracer = InMemoryTracer()
    with TestClient(create_app(settings, tracer)):
        assert tracer.shutdowns == 0  # still running
    assert tracer.shutdowns == 1


def test_each_app_gets_its_own_bus(settings: Settings) -> None:
    first, second = create_app(settings, InMemoryTracer()), create_app(
        settings, InMemoryTracer()
    )
    with TestClient(first), TestClient(second):
        assert first.state.services.bus is not second.state.services.bus
