"""WorkflowWeaver FastAPI application entrypoint."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Vite dev-server origins. The browser sends these as the Origin header; without a match
# the browser blocks the frontend from reading API responses (including the SSE stream).
FRONTEND_DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]

app = FastAPI(title="WorkflowWeaver")
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_DEV_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Liveness probe used by the UI and by evaluators to confirm the backend is up."""
    return {"status": "ok"}
