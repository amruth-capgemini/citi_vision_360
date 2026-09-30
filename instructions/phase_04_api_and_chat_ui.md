# Phase 4: FastAPI backend and React chat UI

> Index: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md). The invariants and
> protected results in index §3 apply to this phase.

**Goal:** a chat UI that shows the answer, the evidence behind every fact, and
which agents, tools and sources were used. The dashboard is out of scope.

**Prerequisites:** [Phase 3](phase_03_langgraph_agents.md) is complete. Node.js
is installed.

## Tasks

1. Dependencies: `api` extra (`fastapi`, `uvicorn`).
2. `src/citi_project/api/app.py`:
   - `POST /api/chat {session_id, question}` returns `{status, final_answer, facts,
     evidence, flags, limitations, specialists_used, sources_used, trace}`.
   - `GET /api/chat/stream?session_id&question` sends Server-Sent Events of
     LangGraph node events, so the UI shows agents as they run.
   - `GET /api/sources` returns a catalog summary (systems, datasets, domains).
   - `GET /api/health` checks Postgres, Neo4j and model configuration, with no
     secrets in the response.
   - Sessions live in an in-memory map `session_id → ConversationState` with a TTL
     and a size cap. Nothing is written to data sources.
   - Add the `citi-api` script (uvicorn). CORS is enabled only for the Vite dev
     origin.
3. `frontend/` (React, Vite, TypeScript):
   - **Chat pane:** messages, a status badge (answered, clarification, not found,
     unsupported, unavailable), and example questions.
   - **Agents panel:** a live timeline of supervisor → specialists → tools →
     sources, with durations, from the SSE feed.
   - **Evidence panel:** each fact card with its source (`schema.table` and line,
     graph relationship, or document and page). Flags and limitations are always
     visible and never hidden.
   - The Vite dev server proxies `/api` to the FastAPI port.

## Tests

- `TestClient` API tests with a scripted model and fake services, covering the
  response contract, the session follow-up, and SSE event order.
- Manual demo: the 15 scripted questions render with evidence and the agent trace.

## Gate

The end-to-end demo in index §5 works on a fresh shell.
