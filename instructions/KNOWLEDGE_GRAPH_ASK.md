# Deterministic Knowledge Graph Ask POC

`KnowledgeGraphAskService` accepts a natural-language question and delegates only
to the five public read methods of `KnowledgeGraphQueryService`. No LLM, new
dependency, generated Cypher, database writes, or automatic connection is involved.

```python
from citi_project.services.knowledge_graph.ask import KnowledgeGraphAskService

# query_service is an existing namespace-bound KnowledgeGraphQueryService.
ask = KnowledgeGraphAskService(query_service)
response = ask.ask("What is the renewal notice for clause renew1 in CTR-001?")
print(response.to_dict())
```

## Flow and supported inputs

`route_question` normalizes whitespace, identifies a supported intent, and extracts
canonical IDs. `ask` dispatches through a fixed allowlist, selects returned facts,
resolves evidence for their nodes and directed edges, and renders fixed templates.
Namespace is supplied by the caller's query service, never by question text.

| Intent | Required selector | Method |
| --- | --- | --- |
| Vendor contracts | One V-001-style ID | get_vendor_contracts |
| Dependencies, services, applications, portal, configuration items, processes, products | One CTR-001-style ID | get_contract_dependencies |
| Clauses, renewal, notice, termination, payment terms | One contract ID; optional `clause renew1 in CTR-001` | get_contract_clauses |
| Risk, findings, SLA, performance, breaches | One contract ID | get_contract_risk_and_sla |
| Evidence, provenance, source | Explicit entity or directed relationship | get_evidence_for_entity_or_relationship |

Evidence forms include `Show evidence for Contract CTR-001`,
`Show evidence for RenewalClause renew1 in CTR-001`, and
`Show evidence for PARTY_TO from Vendor V-001 to Contract CTR-001`.
Relationship direction must be explicit. Clause occurrences are case-sensitive;
vendor/contract IDs and standard numeric IDs are normalized to uppercase.
Document IDs retain their original case. Existing identity/ontology validators
validate evidence selectors. Business-name resolution is not supported.

Ambiguous query types, multiple IDs, missing IDs, and malformed evidence selectors
return clarification without a graph call. Unknown question types are unsupported.
This is a keyword/phrase router, not general language understanding; negation,
comparisons, arbitrary filters, multi-question requests, and reasoning are not
supported. Supported questions should follow the demonstrated forms.

## Response

`AskResponse.to_dict()` is JSON-serializable and includes:

- `status`: answered, needs_clarification, unsupported, or error.
- `namespace`, `intent`, and concise `answer` with F/E references.
- `facts_used`: fact ID, collection, full selected path with properties/revisions,
  and linked evidence IDs. Distinct paths remain distinct facts.
- `evidence`: evidence ID, exact subject selector, found flag, original citation
  fields, resolved documents, and explicit EVIDENCED_BY paths.
- `limitations`: structured code/message entries for missing facts/provenance,
  unresolved documents, truncation, request bounds, and source review status.

The layer preserves SUPPORTS versus USES_PORTAL and inverse risk-edge directions.
Risk on a shared application is not described as a direct vendor finding.
Evidence documents are metadata only; the layer does not retrieve source text.
Synthetic/unreviewed citations are flagged and never promoted to verified evidence.
Even approved review state alone does not establish verified source evidence.

Default bounds: 1,000 question characters, 20 path facts, 100 deduplicated evidence
lookups, in addition to the query service's collection limit. Constructor bounds
permit at most 100 facts and 200 evidence lookups. A business request makes one
business call plus bounded evidence calls; an explicit evidence request makes one
call. Omitted facts/evidence and service truncation are disclosed. Facts and
evidence are separate reads without a shared snapshot guarantee. Backend failures
produce a sanitized error response without a partial answer.

## Offline demonstration

The ten questions are:

1. Which contracts belong to vendor V-001?
2. Which contracts belong to vendor V-002?
3. What services and applications are linked to CTR-001?
4. What dependencies does CTR-002 have?
5. List all clauses for CTR-001.
6. List all clauses for CTR-002.
7. What is the renewal notice for clause renew1 in CTR-001?
8. What SLA performance and risk findings are recorded for CTR-001?
9. Show evidence for RenewalClause renew1 in CTR-001.
10. Show evidence for PARTY_TO from Vendor V-001 to Contract CTR-001.

Tests use a spec-constrained mock query service backed by
`tests/fixtures/knowledge_graph/representative-update.json`. They do not connect
to Neo4j or prove server-side query execution. Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests/test_graph_ask.py -q -p no:cacheprovider
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -B tests/test_graph_ask.py
```

Live integration tests remain opt-in. No CLI command or UI is added by this POC.
