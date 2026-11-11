# WorkflowWeaver

NL-to-multi-tool workflow automation agent (FDE Capstone). Users describe a process in English;
a LangGraph agent executes it across Notion MCP, Google Drive MCP and Microsoft Teams webhook,
streams progress over SSE, traces to Langfuse, retries failures and rolls back on fatal errors.

Problem statement: `D:\CapstoneProject\WorkflowWeaver_ProblemStatement.html`
Implementation plan: `C:\Users\atulg\.claude\plans\you-are-a-senior-sparkling-sprout.md`

## Pass gates (never break these)
1. At least one workflow runs end-to-end.
2. ALL of Notion, Drive, Teams integrated.
3. Langfuse traces emitted.
4. README has: Project Overview, Architecture Description, Setup Instructions, Environment Variables,
   Frontend Run Instructions, Backend Run Instructions, Sample Workflow Examples, Known Limitations.
5. `.env.example` lists every variable used.

## Stack
- Backend: Python 3.10+, FastAPI, LangGraph, langchain-anthropic, langchain-mcp-adapters, Langfuse, SQLite.
- Frontend: React + Vite + Tailwind CSS **v3** (do not upgrade to v4).
- MCP servers (Notion, Drive) run in Docker. Teams is a plain webhook POST (no Graph API).

## Commands
- Backend: `cd backend && python -m venv .venv && pip install -r requirements.txt && uvicorn app.main:app --reload`
- Frontend: `cd frontend && npm install && npm run dev`
- MCP servers: `docker compose up -d`
- Tests: `cd backend && pytest`

## Architecture rules
- Graph code lives in `backend/app/agent/`; one node per file in `nodes/`; state only in `state.py`.
- Every tool goes through the `ToolAdapter` interface (`execute` + `undo`). Never call Notion/Drive/Teams
  directly from a node.
- Every executed action records its undo handle in state BEFORE the next step runs.
- Teams notification is always the final step (it cannot be undone).
- Nodes emit events via `app/events.py`; never write to the SSE stream directly.
- Retry: exponential backoff with jitter, max 3. Only retry transient errors; fatal errors go to rollback.
- LLM outputs for plans/intents must use structured output validated by Pydantic. No regex parsing of prose.
- Prompts live in `agent/prompts.py` only.
- Keep a failure-injection switch (`SIMULATE_FAILURE_STEP`) so retry/rollback can be demoed deterministically.

## Observability
One Langfuse trace per run, tagged with run_id; nested spans per node and per tool call. Flush on shutdown.

## Security and data (strict)
- Never commit secrets. Secrets only in `.env` (gitignored). Never print or log token values.
- Use **synthetic data only** (assessment requirement). No real people, customers or company info
  in samples, tests, prompts or screenshots.
- Every new env var must be added to `.env.example` and the README table in the same change.

## Code style
- Python: type hints, async/await for I/O, ruff + black.
- React: function components + hooks, Tailwind utilities only.
- Comment the *why* of non-obvious logic (code quality is graded). Keep modules small.

## Definition of done for a feature
Works against the real service, has a test for its logic, emits events and a Langfuse span,
README and `.env.example` updated.

## Packaging
Final ZIP: `README.md`, `.env.example`, `frontend/`, `backend/`, `docs/architecture.png`.
Exclude `node_modules`, `.venv`, `.env`, `credentials.json`, token files, `*.db`.

## Grading weights (prioritise accordingly)
Functional correctness 35% · Agent design & orchestration 25% · UX & real-time feedback 20% ·
Code quality & docs 15% · Innovation 5%.
