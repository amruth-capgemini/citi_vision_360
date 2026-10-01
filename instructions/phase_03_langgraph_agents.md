# Phase 3: LangGraph orchestration with exploring sub-agents

> Index: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md). The invariants and
> protected results in index §3 apply to this phase. The LLM boundary was revised
> for this phase on 2026-09-30 (index §3, "LLM boundaries").

**Goal:**
- A LangGraph orchestrator delegates each question to specialist sub-agents.
- Each sub-agent first calls its certified tool.
- It then explores with its own read-only SQL and Cypher: it finds sources in
  the metadata catalog, retries when a query fails or returns nothing, and
  follows foreign-key links to reach related records.
- Every run records which agents, queries and sources were involved.

**Prerequisites:** Phases [0](phase_00_routing_fix.md),
[1](phase_01_postgres.md) and [2](phase_02_metadata_graph.md) are complete.

## Status (2026-09-30)

Implemented. The offline suite is green (591 passed, 25 skipped). Live runs against
local Postgres, Aura and Azure `gpt-5.4-mini` answer with the protected values,
and the explorer retries, searches the catalog and follows links. Examples: the
V-009 renewal explorer reached its clauses in the graph, and the India scenario
explorer found the four contractor rows.

**Gate not yet passed.** `tests/integration/test_agents_live.py` passes 4–5 of 6
per run. The failures are in planning and routing, not exploration:
- a specialist sometimes declines a supported question
- the router sometimes emits a mention that does not resolve

The proposed fix, awaiting a decision, is one bounded retry of a declined or
invalid specialist plan, and of a route whose mention does not resolve. Retried
output passes the same validation.

As built (differences from the plan below):
- **Dates:** each explorer task carries `as_of_date` (the data snapshot,
  2026-09-28), and the prompt anchors windows on it. `current_date` and `now()`
  stay refused, so no query can use the wall clock.
- **Vendor-scoped specialists** (`vendor360`, `risk_dependency`,
  `spend_forecast`) are dropped when no vendor is named but a portfolio
  specialist is also routed. The limitation is `specialist_skipped`. If only
  vendor-scoped specialists are routed, the result is still clarification.
- **A specialist that declines** (clarification or no calls) is dropped when
  another specialist has a valid plan (limitation `specialist_declined`). An
  invalid call still fails the whole plan closed.
- **Column resolution follows SQL scoping.** A column resolves in its own SELECT
  and then outer ones, never against another UNION branch's table. Unknown
  columns suggest close matches.
- **One node per sub-agent.** Each specialist has its own node, `plan_<name>`
  (e.g. `plan_renewal`). Its explorer is a sub-graph node, `explore_<name>`
  (discover → decide → act → report), which Studio can expand. The supervisor
  dispatches to them with `Send`.
- **Written answers.** The model writes a summary and headed paragraphs, each
  citing its fact cards. `grounding.verify_narrative` checks every value and
  gives one retry with hints ("it is in F1; add that card"). After that the
  answer falls back to the cards. Host notes are appended as a bulleted list,
  and the result keeps `narrative` and `synthesis_checks`. Money in cards is
  formatted as USD 5,738,383.54; the source values are unchanged. Prompt
  `decision-agents-v4`. Live: 13 of 15 agent tests pass, and every written
  answer passed the checks. The 2 failures are the known planning variance.
- **Evidence per dynamic query.** Each query is one evidence entry:
  - `query_language` (`sql`|`cypher`) and the exact `query` that ran
  - `datasets`, or `graph` and `namespace`
  - `specialist`, `purpose`, `columns`, `rows`, `row_count` and `truncated`
  - for SQL, `records` (`dataset:line N`), filled only for single-dataset
    queries

  Every fact card from that query cites this entry. Queries that failed or were
  rejected appear in `trace`, not in the evidence.
- **Prompts:** specialist prompt `decision-agents-v3` (a follow-up's vendor scope
  is settled by the host), explorer prompt `explorer-v3`.
- **CLI:** `citi-agent ask "<question>" [--trace] [--json F] [--no-explore]`.
- **LangGraph Studio:** `uv run langgraph dev --allow-blocking`
  - `langgraph.json` points at `services/agents/studio.py:graph`, and loads
    `.env` itself.
  - Input: `{"question": "...", "active_vendor_id": null}`. Output:
    `final_answer`, `result` and `trace`.
  - `--allow-blocking` is needed because the graph uses synchronous psycopg and
    Neo4j calls.

**Design decisions (agreed 2026-09-30):**
- Agents may write dynamic SQL (Postgres) and Cypher (Neo4j). The guards are
  code, not prompts.
- The six Decision Intelligence tools remain the **certified** path for the
  protected metrics. Dynamic queries add context. If a dynamic value disagrees
  with a certified one, the answer flags a contradiction instead of choosing one.
- The host renders every factual sentence, from query rows or certified results.
  The model selects which observations and fact cards to use and never writes a
  number.
- There is one orchestration graph with no fallback orchestrator. There is no
  checkpointer: the caller owns `ConversationState`.
- The explorer is optional. Without executors the graph behaves exactly as
  Phase 0 did, so the routing and guardrail tests stay valid.

## Architecture

```
guard → route → resolve → plan (fan-out: one LLM call per specialist)
      → validate (all certified calls, before any runs) → execute (certified tools)
      → explore (fan-out: one explorer sub-graph per specialist, if enabled)
      → verify ─ gaps and not yet retried ─► explore again, for the gaps only
      → ground → synthesize → render (+ trace, sources_used)
```

Early stops (unsupported, clarification, not_found, unavailable) are
conditional edges to a single `stop` node, with the same statuses and messages
as before.

**Explorer sub-graph** (one per specialist task):

```
discover (catalog search for the task's concepts; no LLM)
   → step (LLM chooses: search_catalog | run_sql | run_cypher | finish)
   → guard (code) → execute (read-only) → observe (rows, links, concepts)
   → step … until finish or the budget runs out
```

Hand-offs:
- **Task** (orchestrator to sub-agent): `task_id`, `specialist`, `objective`,
  `vendor_ids`, `required_concepts`, `certified` (the fact texts already
  returned), `gaps` (only on re-delegation) and `budget`.
- **Findings** (sub-agent to orchestrator): `task_id`, `status`
  (`complete|partial|failed`), the selected observations (query, datasets, rows),
  `links_followed`, `gaps`, `queries_run` and `trace`.

## Tools and guards

| Tool | Guard (code) |
|---|---|
| `search_catalog(concepts, terms)` | Reads the in-memory `SchemaIndex`, which is loaded once from the catalog namespace. |
| `run_sql(query)` | See **SQL guard** below. |
| `run_cypher(graph, query)` | `graph` is `business` or `catalog`. See **Cypher guard** below. |

**SQL guard:**
- The query is parsed with `sqlglot` (Postgres dialect). It must be a single
  statement and a query (`SELECT`, `WITH`, `UNION`).
- These are rejected anywhere in the tree: DML, DDL, `COPY`, `SET`,
  `SELECT … INTO` and locking clauses.
- Every table must be a catalog dataset. Every column must exist in a referenced
  dataset, or be an alias. Masked columns are refused, and `*` is refused on
  datasets that have masked columns.
- Functions are allow-listed. `pg_read_file`, `dblink`, `set_config`,
  `current_date` and the like are refused.
- Identifiers are re-quoted to their catalog spelling, which fixes
  `Contract_ID` versus `contract_id`.
- The SQL that runs is regenerated from the checked tree and wrapped with
  `LIMIT` = row cap + 1.
- It runs as `citi_reader` in a read-only transaction, with
  `statement_timeout = 5s`.

**Cypher guard:**
- Comments are removed and strings masked before checking.
- Refused: write and admin clauses (`CREATE`, `MERGE`, `SET`, `DELETE`,
  `REMOVE`, `DROP`, `LOAD`, `FOREACH`, `CALL`, `USE`, `SHOW`, …) and the
  `apoc.`, `gds.`, `dbms.` and `db.` namespaces.
- Every pattern must be anchored on a node with `_kg_namespace: $namespace`, or
  on a variable already anchored. Relationships never cross namespaces. The host
  binds `$namespace`.
- PII properties are refused, and are removed from returned nodes.
- It runs with `execute_read`, and the host stops reading after row cap + 1.

**Budgets:**
- Per specialist: up to 6 queries, reduced so that the whole question uses at
  most 15. Each query may be retried twice, and link depth is at most 3.
- The explorer also has a step cap. A query row cap of 50 applies; the model is
  shown at most 20 rows.

**Failure isolation:** exploration is supplementary. A guard rejection, query
error or model failure inside an explorer adds a limitation, and the certified
answer still returns.

## Tasks

1. Dependencies: `langgraph` and `sqlglot` are core dependencies, because the
   orchestrator is core. `openai` stays in the `agents` extra.
2. `services/agents/query_guard.py`: `SchemaIndex`, `guard_sql`,
   `guard_cypher` and `QueryRejected`.
3. `services/agents/executors.py`:
   - `SqlExecutor` (psycopg, reader DSN, read-only, timeout, row cap)
   - `CypherExecutor` (the existing graph client `read`, namespace binding,
     value conversion, PII stripping)
4. `services/agents/explorer.py`: the explorer sub-graph, step schema, budgets,
   observations, link hints from `REFERENCES`, and findings.
5. `services/agents/graph.py`: the orchestration `StateGraph`. The
   `SupervisorAgent.ask()` signature is unchanged, and the explorer is optional.
6. `services/catalog/query.py`: a fixed `schema_index()` read that feeds
   `SchemaIndex`.
7. Grounding:
   - fact cards for dynamic observations, rendered by the host with dataset and
     `_source_line` evidence
   - a `contradiction` limitation when a dynamic value disagrees with a
     certified one
8. The result gains:
   - `trace`: one entry per node and per explorer step, with agent, action,
     purpose, query, datasets, rows, status and duration
   - `sources_used`
   - `findings`
9. A `citi-agent ask "<question>"` CLI for the live gate. It wires Postgres, Aura
   and Azure from the environment.

## Tests

- Offline:
  - all current routing and guardrail tests pass unchanged through the graph
  - SQL guard rejections: writes, DDL, multiple statements, `INTO`, locks,
    data-modifying CTEs, unknown tables and columns, masked columns, `*` on
    masked datasets, disallowed functions
  - SQL guard acceptance: identifier re-quoting, and the `LIMIT` wrapper
  - Cypher guard: rejects write clauses, `CALL`, unanchored patterns, a literal
    namespace and PII properties; accepts anchored multi-hop patterns and
    re-anchored `WITH` aliases
  - explorer:
    - retries after an error or 0 rows, and stops at the retry budget
    - follows links up to depth 3
    - query and step budgets
    - model failure isolation
    - findings rendered by the host
  - orchestrator: parallel explorers, re-delegation on gaps (once only), a
    contradiction flag, and the trace covering every node
- Live (opt-in, Azure + Postgres + Aura):
  - "What do we know about Aurelix Codeworks?"
  - "Why is V-005 above budget?"
  - "Give me renewal, risk and spend context for V-009."
  - "What if India contractors are reduced by 20%?"
  - the V-005 → "What about its workforce?" follow-up
  - "Which contracts end in the next 90 days, and what forecast and workforce
    records are linked to them?"

  Each should be `answered`, with the expected specialists, protected values and
  no contradiction.

## Gate

- The offline suite is green.
- The live questions pass, and `trace` shows the specialists, the certified
  tools, and the dynamic queries with the links they followed.
