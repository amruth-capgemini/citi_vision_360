# Phase 0: Routing fix and Azure OpenAI adapter

> Index: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md). The invariants and
> protected results in index §3 apply to this phase.

**Goal:** make live supervisor routing work on the user's Azure OpenAI deployment
without weakening any guardrail.

**Prerequisites:** none for the offline work. The live gate needs the Azure
environment variables (index §6) and the `openai` SDK (`requirements-agents.txt`).

Phase 0 goes first because it unblocks every live test after it. It is
implemented inside the current `SupervisorAgent` and carried into LangGraph in
[Phase 3](phase_03_langgraph_agents.md).

## Root cause

With `gpt-4.1-nano`, all three live questions returned `route_not_actionable`.
The model picked the correct `focus` but set `status` to `clarification` or
`unsupported`:

- "Why is V-005 above budget?"
- "What if India contractors are reduced by 20%?"
- "What do we know about Aurelix Codeworks?"

There were three causes:
1. `prompts.SUPERVISOR` never said when to return `route`.
2. The `BOUNDARY` rule "never calculate spend/variances/counts/savings" read to
   the model as "these questions are out of scope".
3. `status` was the first property in `ROUTE_SCHEMA`, so under strict output the
   model committed to a status before it reasoned about focus and specialists.

Entity resolution and the validators were *not* the cause, because they run after
the status check.

## Tasks

1. `services/agents/prompts.py`:
   - Bump `PROMPT_VERSION` to `decision-agents-v2`.
   - Reword `BOUNDARY` to say "never compute these yourself; approved tools
     compute them, so questions asking for them are supported".
   - Rewrite `SUPERVISOR` around a positive default: return `route` whenever the
     question maps to a capability, with one example question per specialist. For
     example, "why is X above budget" goes to spend_forecast, because the tool
     reports the variance and states when the cause is unavailable.
   - State that portfolio questions need no vendor: expiry lists,
     rationalization, and geography or workforce counts and scenarios.
   - Say vendor names are copied verbatim into `entity_mentions`.
   - Limit `clarification` to three cases: (a) a vendor-scoped capability with no
     vendor and no active context, (b) a workforce change with neither a
     percentage nor a count, (c) unintelligible text.
   - Limit `unsupported` to write requests and topics outside all capabilities.
2. `services/agents/contracts.py`: reorder `ROUTE_SCHEMA` properties to
   `focus, specialists, entity_mentions, use_active_entity, status`.
3. `services/agents/supervisor.py`: add the validated `route` object to every
   result, including early stops, for diagnosis. Control flow stays unchanged.
4. **Azure OpenAI adapter** in `services/agents/openai_model.py`:
   - `AzureOpenAIModelConfig.from_env()` reads `AZURE_OPENAI_ENDPOINT`,
     `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_API_VERSION` (default `2024-10-21`,
     and at least 2024-08-01 for strict structured outputs) and
     `AZURE_OPENAI_DEPLOYMENT`.
   - `AzureOpenAIJsonModel` uses `openai.AzureOpenAI(azure_endpoint, api_key,
     api_version, timeout, max_retries=0)`, with the deployment passed as `model`.
   - Move the shared request, response and error handling into one helper, so
     the OpenAI and Azure adapters keep identical bounds, `store=False`,
     finish-reason checks and sanitized errors.
   - The key is read lazily on first use, and no secrets appear in repr or errors.
   - `select_model()` returns the Azure adapter when its environment variables
     are set, and the OpenAI adapter otherwise.
   - The catalog enricher ([Phase 2](phase_02_metadata_graph.md)) and the agents
     share this adapter through the `JsonModel` protocol.

## Tests

- Unit tests:
  - `status` is last in `ROUTE_SCHEMA.required`
  - `SUPERVISOR` defines `route`
  - `result["route"]` is present on both answered and stopped results
  - all existing guardrail tests pass unchanged
- `tests/test_openai_agent_model.py`, with a mocked Azure client, checks:
  - the endpoint, version and deployment are passed through
  - missing configuration raises `ModelError`
  - errors are sanitized
  - the input bound is enforced
  - `select_model()` picks the right adapter
- `tests/integration/test_openai_agents.py`:
  - checks the route on its own, so failures point to a stage
  - adds "Give me renewal, risk and spend context for V-009."
  - adds the follow-up sequence (V-005, then "What about its workforce?")
  - uses `select_model()`, and skips unless Azure or OpenAI configuration is visible

## Status

All tasks are implemented: the prompt, schema and `route` changes, and the Azure
adapter with `select_model()`. The offline suite passes with 434 passed and 11
skipped; the skips are 2 Neo4j tests and 9 opt-in live model tests. **The live
gate is still pending**, because it needs the Azure environment variables in the
user's shell.

## Gate

The offline suite is green, and the live run passes on the Azure deployment:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-agents.txt
# In a fresh shell, set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY (Read-Host -AsSecureString),
# AZURE_OPENAI_API_VERSION and AZURE_OPENAI_DEPLOYMENT.
.venv\Scripts\python.exe -B -m pytest tests/integration/test_openai_agents.py --openai-integration -q -rs -p no:cacheprovider
```

If a nano-sized deployment still routes badly, rerun against a larger deployment
(for example gpt-4.1-mini or gpt-4o) through configuration only, and report both
results. The recorded `route` in the assertion message shows the model's choice.
