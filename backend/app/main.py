"""WorkflowWeaver FastAPI application entrypoint."""

from fastapi import FastAPI

app = FastAPI(title="WorkflowWeaver")


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Liveness probe used by the UI and by evaluators to confirm the backend is up."""
    return {"status": "ok"}
