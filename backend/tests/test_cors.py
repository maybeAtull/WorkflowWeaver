from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_allows_vite_dev_origin() -> None:
    response = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_preflight_is_accepted_for_vite_dev_origin() -> None:
    response = client.options(
        "/api/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_other_origins_get_no_cors_header() -> None:
    response = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in response.headers
