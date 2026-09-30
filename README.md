# Citi Vision 360

A read-only vendor decision-intelligence platform:
- PostgreSQL holds the structured facts.
- A Neo4j metadata graph indexes which source holds which information.
- LangGraph specialist agents choose the right sources and return grounded answers.
- A chat UI shows the evidence and the agents involved.

The YAML ontology is the source of truth for business classes, properties and
directed relationships.

See **[the Implementation Plan](instructions/IMPLEMENTATION_PLAN.md)** for:
- the target architecture and current state
- invariants and protected results
- configuration and end-to-end verification

Each phase has its own file with tasks and a gate:
- [Phase 0: routing fix and Azure OpenAI adapter](instructions/phase_00_routing_fix.md)
- [Phase 1: PostgreSQL](instructions/phase_01_postgres.md)
- [Phase 2: metadata graph](instructions/phase_02_metadata_graph.md)
- [Phase 3: LangGraph agents](instructions/phase_03_langgraph_agents.md)
- [Phase 4: API and chat UI](instructions/phase_04_api_and_chat_ui.md)

Layout:
- `backend/`: the Python project (services, agents, CLIs, tests, ontology)
- `frontend/`: the chat UI (Phase 4)
- `initial_plan/`: the synthetic data pack
- `instructions/`: the plan

```powershell
cd backend
uv sync --locked
uv run pytest -q
uv run citi-kg describe
uv run citi-kg sample --output kg-sample.json
uv run citi-kg ingest kg-sample.json --dry-run
```

The commands above do not connect to Neo4j. Database operations are explicit and require
environment configuration. No PDF extraction pipeline is implemented by this feature.
