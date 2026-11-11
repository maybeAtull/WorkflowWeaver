# WorkflowWeaver – Implementation Plan

## Context
FDE Capstone (30–40 hrs): a platform where a business user types a workflow in plain English and a LangGraph agent executes it across **Notion (MCP), Google Drive (MCP), Microsoft Teams (webhook)**, streaming progress over SSE, with Langfuse tracing, retry and rollback. Graded by an automated DIFY workflow on: Functional correctness 35%, Agent design 25%, UX 20%, Code quality/docs 15%, Innovation 5%.

**Hard pass gates** (everything is prioritised around these): (1) one workflow runs end-to-end, (2) ALL three tools integrated, (3) Langfuse traces present, (4) complete README with the 8 required sections, (5) valid `.env.example`. Also: ZIP structure `README.md, .env.example, frontend/, backend/, docs/architecture.png`; synthetic data only (disclaimer in the problem statement); no secrets committed.

Workspace `d:\CapstoneProject\WorkflowWeaver` is empty and not a git repo. Plan: `git init` first (rollback safety for us, and `.gitignore` for secrets).

## Recommended decisions (and why)
| Decision | Choice | Why |
|---|---|---|
| Backend | **FastAPI (Python)** | LangGraph, Langfuse and MCP SDKs are Python-first; one language for the whole agent side. |
| LLM | **Claude Sonnet via `langchain-anthropic`** | Rubric says "how effectively Claude is prompted"; strong tool use and structured output. Keep the model name in `.env` (`LLM_MODEL`) so it's swappable. |
| MCP client | **`langchain-mcp-adapters`** (stdio to Docker containers) | Converts MCP tools into LangChain tools that LangGraph can call; satisfies "demonstrably MCP". |
| Notion/Drive MCP | Official `@notionhq/notion-mcp-server`, a Drive MCP server, both in Docker | Required by the brief; Docker Compose makes setup reproducible for the evaluator. |
| Teams | Plain `httpx` POST of an Adaptive Card to the Workflows webhook | Graph API explicitly not required. Not MCP, so it's a normal tool wrapper. |
| Run store | **SQLite** (runs, steps, reports) | Zero setup for the evaluator; supports history and the JSON report endpoint. |
| Frontend | React + Vite + **Tailwind v3** (pinned) | Brief says v3; v4 config differs and would confuse the setup instructions. |
| Streaming | SSE with `sse-starlette`; frontend uses `fetch` stream or `EventSource` | Required. Run is started with POST, events read with GET `/runs/{id}/events`. |

Open risk to resolve early: **Google Drive MCP needs OAuth** (browser consent, token caching) which is awkward inside Docker. Mitigation: do the OAuth flow once on the host, mount the token file into the container, and document it. Do this in Phase 1, not at the end.

## Target structure
```
WorkflowWeaver/
├── CLAUDE.md            .env.example   .gitignore   README.md   docker-compose.yml
├── backend/
│   ├── app/main.py                 # FastAPI app, CORS, routers
│   ├── app/api/{runs,templates,reports}.py
│   ├── app/agent/{state.py,graph.py,prompts.py}
│   ├── app/agent/nodes/{parse_intent,plan,execute_step,verify,rollback,report}.py
│   ├── app/tools/{notion.py,drive.py,teams.py,registry.py}   # uniform Tool interface incl. undo()
│   ├── app/observability/langfuse_setup.py
│   ├── app/events.py               # per-run event bus -> SSE
│   ├── app/db.py  app/config.py  app/templates/*.json
│   └── tests/
├── frontend/src/{components,hooks/useRunStream.js,pages,api}
├── docs/architecture.png  docs/*.md
└── samples/                        # synthetic meeting notes
```

## Phases and steps

### Phase 0 – Setup and de-risking (≈2h)
1. `git init`, `.gitignore` (`.env`, `credentials.json`, `token.json`, `node_modules`, `.venv`, `*.db`).
2. Scaffold backend (venv, `requirements.txt`: fastapi, uvicorn, sse-starlette, langgraph, langchain-anthropic, langchain-mcp-adapters, langfuse, httpx, pydantic-settings, pytest) and frontend (`npm create vite`, Tailwind v3).
3. Write `.env.example` with the exact variables from the brief (+ `LLM_MODEL`, `LLM_PROVIDER`). Pydantic `Settings` that fails fast with a clear message on missing vars.
4. Create accounts/credentials with **synthetic test data only**: Notion integration + shared page, Google Cloud OAuth + Drive folder, Teams Workflows webhook, Langfuse project.
5. **Smoke-test each integration in isolation** with throwaway scripts: list Notion tools via MCP and create a page; upload a Drive doc; POST a Teams card; emit one Langfuse trace. *Why: every downstream phase depends on these; finding an OAuth/Docker problem on day 1 is cheap, on the last day fatal.*

### Phase 1 – Tool layer (≈5h)
6. `docker-compose.yml` for the Notion and Drive MCP servers; backend MCP client manager (start/connect/close, lifecycle in FastAPI lifespan).
7. Define a `ToolAdapter` interface: `execute(params) -> ToolResult{ok, output, undo_handle}` and `undo(undo_handle)`. Implement Notion (create page, create tasks/DB rows; undo = archive/delete page), Drive (create doc; undo = delete file), Teams (post card; undo = no-op, documented as non-reversible, so Teams is always the **last** step).
8. Central registry exposing a tool catalogue to the planner. *Why: the undo handle is what makes rollback real rather than cosmetic, and Functional Correctness (35%) explicitly tests rollback.*

### Phase 2 – LangGraph agent (≈8h, the heart of the 25% criterion)
9. `WorkflowState` (TypedDict): `user_request, inputs, plan[], current_idx, step_results[], completed_actions[] (with undo handles), retries, status, error, report`.
10. Nodes: `parse_intent` (LLM, structured output → intent + extracted entities) → `plan` (LLM, structured output → ordered steps `{id, tool, action, params, depends_on}`, validated against the tool catalogue) → `execute_step` (calls adapter; may use LLM to generate content such as summaries/action items from meeting notes) → conditional edge: success→next step / retryable failure→`retry` (exponential backoff + jitter, max N) / fatal→`rollback` → `verify` (check outputs, e.g. page URL exists) → `report` (JSON report).
11. `rollback` node walks `completed_actions` in reverse, calling `undo`, recording each outcome.
12. Prompts in `prompts.py` (versioned constants, few-shot for plan format). Add a **failure-injection** switch (`SIMULATE_FAILURE_STEP`) so retry and rollback can be demoed deterministically.
13. Unit-test routing logic with mocked adapters (no LLM needed).

### Phase 3 – Observability (≈2h)
14. Langfuse `CallbackHandler` on the graph invocation, one **trace per run** (`run_id`, template name as metadata/tags), nested spans per node and per tool call (`@observe` on adapters), tool input/output captured, scores for success/retry count. Flush on shutdown. *Why: "well-structured traces" is graded; a flat pile of LLM calls is not.*

### Phase 4 – API + SSE (≈4h)
15. Endpoints: `POST /api/runs`, `GET /api/runs/{id}/events` (SSE), `GET /api/runs/{id}/report`, `GET /api/runs` (history), `GET /api/templates`, `GET /api/health`.
16. Run graph as a background asyncio task; nodes publish typed events (`step_started, step_succeeded, step_retry, step_failed, rollback_started, rollback_step, run_completed`) to an in-memory per-run queue; SSE drains it. Persist events so late subscribers/refresh can replay.
17. 3+ templates as JSON (Meeting-to-Tasks, Sprint Planning, Incident Response) with parameter placeholders.

### Phase 5 – Frontend (≈7h, 20% criterion)
18. Layout: request box + template gallery (click fills and exposes parameter fields: Notion parent, Drive folder, Teams channel label), run button.
19. Live execution timeline driven by `useRunStream`: per-step status icons, retry/rollback visibly distinct, auto-scroll log, connection-lost handling.
20. Report view (human-friendly summary with links to created Notion/Drive items + raw JSON toggle + download), run history page.
21. Business-user polish: plain-language errors, loading/empty states, no jargon. *Why: the rubric says "non-technical business user".*

### Phase 6 – Hardening and bonus (≈3h)
22. End-to-end test of all three templates against real services; test retry (injected transient failure) and rollback (injected fatal failure at the Teams/Drive step, confirm the Notion page is archived).
23. Pick **one or two** cheap bonus features (5% only): plan preview with approve/edit before execute (human-in-the-loop via LangGraph `interrupt`), or a Mermaid view of the plan. Skip scheduling and a visual builder.

### Phase 7 – Docs and packaging (≈3h, gates + 15%)
24. `README.md` with all 8 mandated sections (Overview, Architecture, Setup, Env vars, Frontend run, Backend run, Sample workflows, Known limitations).
25. `docs/architecture.png` (export a diagram; required path) plus a LangGraph graph diagram.
26. Dry-run as the evaluator: fresh clone → follow README only → run a workflow. Scan for secrets. Zip using the required structure, excluding `node_modules`, `.venv`, `.env`, `credentials.json`.

## Verification
- `pytest backend/tests` (routing, retry/backoff, rollback ordering with mocks).
- Manual E2E: Meeting-to-Tasks template with sample notes → Notion page + tasks, Drive doc, Teams card all appear; timeline streams live; report JSON saved.
- Failure drills: injected transient failure → visible retries then success; injected fatal → rollback visible and the Notion page archived.
- Langfuse UI shows one nested trace per run with token/latency/cost.
- Clean-clone README walkthrough passes; `git grep` finds no secrets; zip layout matches spec.

---

# CLAUDE.md (draft to create on approval, each section with its rationale)

```markdown
# WorkflowWeaver

NL-to-multi-tool workflow automation agent (FDE Capstone). Users describe a process in English;
a LangGraph agent executes it across Notion MCP, Google Drive MCP and Microsoft Teams webhook,
streams progress over SSE, traces to Langfuse, retries failures and rolls back on fatal errors.

## Pass gates (never break these)
1. At least one workflow runs end-to-end. 2. ALL of Notion, Drive, Teams integrated.
3. Langfuse traces emitted. 4. README has: Overview, Architecture, Setup, Env Vars, Frontend Run,
Backend Run, Sample Workflows, Known Limitations. 5. `.env.example` lists every variable used.

## Stack
Backend: Python 3.10+, FastAPI, LangGraph, langchain-anthropic, langchain-mcp-adapters, Langfuse, SQLite.
Frontend: React + Vite + Tailwind CSS **v3** (do not upgrade to v4).

## Commands
- Backend: `cd backend && python -m venv .venv && pip install -r requirements.txt && uvicorn app.main:app --reload`
- Frontend: `cd frontend && npm install && npm run dev`
- MCP servers: `docker compose up -d`
- Tests: `cd backend && pytest`

## Architecture rules
- Graph code lives in `backend/app/agent/`; one node per file in `nodes/`; state only in `state.py`.
- Every tool goes through the `ToolAdapter` interface (`execute` + `undo`). Never call Notion/Drive/Teams
  directly from a node.
- Every executed action records an undo handle in state BEFORE the next step runs.
- Teams notification is always the final step (it cannot be undone).
- Nodes emit events via `app/events.py`; never write to the SSE stream directly.
- Retry: exponential backoff with jitter, max 3. Only retry transient errors; fatal errors go to rollback.
- LLM outputs for plans/intents must use structured output validated by Pydantic. No regex parsing of prose.
- Prompts live in `agent/prompts.py` only.

## Observability
One Langfuse trace per run, tagged with run_id; nested spans per node and tool call. Flush on shutdown.

## Security and data (strict)
- Never commit secrets. Secrets only in `.env` (gitignored). Never print or log token values.
- Use **synthetic data only** (assessment requirement). No real people, customers or company info
  in samples, tests, prompts or screenshots.
- Every new env var must be added to `.env.example` and the README table in the same change.

## Code style
Python: type hints, async/await for I/O, ruff + black. React: function components + hooks, Tailwind utilities only.
Comment the *why* of non-obvious logic (code quality is graded). Keep modules small.

## Definition of done for a feature
Works against the real service, has a test for its logic, emits events and a Langfuse span, README/.env.example updated.

## Packaging
Final ZIP: README.md, .env.example, frontend/, backend/, docs/architecture.png. Exclude node_modules, .venv, .env,
credentials.json, token files, *.db.
```

**Why each section is there**
- *Pass gates*: they are pass/fail for the whole capstone; putting them at the top stops Claude from drifting into polish while a gate is unmet.
- *Stack + pinned Tailwind v3*: prevents Claude defaulting to the newer v4 config, which breaks the brief's setup.
- *Commands*: saves rediscovery every session and keeps README instructions consistent with reality.
- *Architecture rules (ToolAdapter, undo-before-next, Teams last)*: these encode the design decisions that make rollback correct, the hardest and most heavily weighted behaviour.
- *Structured-output rule*: avoids fragile prompt parsing, the most common failure in agent projects.
- *Observability rule*: turns the "well-structured traces" criterion into a concrete convention.
- *Security/synthetic data*: the brief forbids real data and committed keys; violating either can fail the submission.
- *Definition of done*: keeps documentation (15%) and observability (25%) from being left for a last-day scramble.
- *Packaging*: the DIFY evaluator rejects non-conforming submissions.

## Total estimate
≈34h, inside the 30–40h budget. Biggest schedule risks: Drive OAuth in Docker (mitigated in Phase 0) and rollback correctness (mitigated by the `undo` interface and the failure-injection switch).
