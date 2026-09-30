# Deterministic structured and graph context

`StructuredQueryService` loads the three current CSVs and five generated mapping
files under `initial_plan`. `VendorSemanticService` combines its results with the
existing `KnowledgeGraphQueryService`. No LLM, generated Cypher, SQL engine, new
dependency, graph mutation, ingestion change, or renewal recommendation is used.
The existing graph Ask router remains unchanged; this phase exposes Python APIs.

```python
from citi_project.services.structured_data import StructuredQueryService
from citi_project.services.semantic_service import VendorSemanticService
from citi_project.services.knowledge_graph.query_service import KnowledgeGraphQueryService
from citi_project.services.knowledge_graph.config import Neo4jConfig
from citi_project.services.knowledge_graph.transport import create_client
from citi_project.services.ontology import OntologyRegistry

structured = StructuredQueryService("initial_plan")
with create_client(Neo4jConfig.from_env()) as client:
    graph = KnowledgeGraphQueryService(
        OntologyRegistry.load(), client, namespace=structured.namespace
    )
    service = VendorSemanticService(structured, graph)
    result = service.get_vendor_renewal_context("V-001")
```

The graph argument is optional. Without it, structured facts still return and
graph-dependent facts are explicitly unavailable. No graph connection occurs at
import or construction. Configuration remains in the existing Neo4j config path;
no credentials are printed or written by these services.

## Public APIs

All public query methods return dictionaries containing `namespace`, `vendor_id`,
`contract_id`, `question_type`, `facts`, `evidence`, and `limitations`. Collection
expiry/grouping results have null vendor/contract IDs. Returned data is detached
from the loaded snapshot. There is no automatic refresh; construct a new service
to load another validated snapshot.

Structured methods:

- `get_vendor_contract(vendor_id)`
- `get_expiring_contracts(days, as_of_date=None)`
- `get_vendor_financials(vendor_id)`
- `get_budget_forecast_actual(vendor_id, year=2026)`
- `get_vendor_financial_variance(vendor_id, year=2026)`
- `get_vendor_forecast(vendor_id)`
- `get_vendor_workforce(vendor_id)`
- `get_vendor_workforce_count(vendor_id)`
- `get_workforce_groups(group_by, vendor_id=None)`
- `get_vendor_services(vendor_id)`
- `get_vendor_products(vendor_id)`
- `get_vendor_organization(vendor_id)`
- `get_vendor_application_bridge(vendor_id)`

Semantic methods:

- `get_vendor_renewal_context(vendor_id, *, as_of_date=None, year=2026)`
- `get_vendor_dependency_context(vendor_id)`
- `get_vendor_dependency_workforce_context(vendor_id)`
- `get_vendor_financial_context(vendor_id, year=2026)`
- `get_vendor_workforce_context(vendor_id, group_by=None)`
- `get_vendor_risk_sla_context(vendor_id)`
- `get_contract_expiry_context(days=90, as_of_date=None)`

`calculate_sla_breach(actual, target, direction)` supports explicitly specified
`higher_is_better` and `lower_is_better`; unknown inputs return null.
Invalid CSVs and unsupported inputs raise `StructuredDataError`. Absent records
return missing-data limitations. Graph/evidence failures preserve structured
results and sanitized limitations; they do not mean absence of a business risk.

## Join and aggregation rules

Only `synthetic-pack-20260928` is loaded. Every join uses canonical IDs within this
namespace. Master rows currently have one contract and one funded service per
vendor; ambiguous duplicates fail validation. All financial/forecast/workforce
rows must match the master and explicit organization/OU crosswalk. No name joins
or archived files are used. Crosswalk dates and current snapshot dates agree.

Financial grain is namespace/contract/year/scenario. Financial values are exact
decimal strings, calculated with Decimal and rounded to cents or two percentage
decimals. Budget and Forecast are full-year 2026; Actual is January–August 2026.
Actual variance uses the Budget row's January–August `YTD_Amount`. Missing Budget
does not fall back to a copied amount from another scenario. A zero baseline
returns null percentage. Monthly sums, annual aliases, comparable budget and
forecast CSV amounts are validated before any result is returned.

`forecast_variance_amount = forecast - budget` and percentage is amount / budget
* 100. Actual variance follows the same rule using comparable YTD amounts.
The forecast CSV is an alternate representation of financial Forecast: never
add these two sources. Applications and workforce remain separate fact groups;
neither is joined onto financial rows. This prevents financial multiplication.

Workforce count is distinct `Assignment_ID` within the representative subset.
Identical duplicate assignments are collapsed; conflicting duplicate IDs fail.
Group by Vendor_ID, Contract_ID, Service_ID, Organization_ID, Product_ID,
WORKER_TYPE or TECH_MARKET_TYPE. `Worker_Type` and `Onshore_Offshore` are accepted
aliases. These counts are not complete enterprise headcount or distinct people.
Financial Product_ID is the canonical allocation product, which may differ from
products enabled by graph applications.

## Dates, risk, SLA, and evidence

Default expiry calculations use the data snapshot date **2026-09-28**, not the
wall clock. The range is inclusive from zero through `days` days (0–3650).
Pass `as_of_date` for another date; it changes expiry calculations, not the dates
of the underlying financial/workforce/risk evidence.

Graph access uses only the existing five public query-service methods. Dependency
paths retain SUPPORTS versus USES_PORTAL and are checked against the CSV bridge.
Graph data and document evidence are separate reads, not an atomic shared snapshot
with CSVs. Each graph collection retains truncation flags. Document lookups are
deduplicated per request and bounded by `max_evidence` (default 80, maximum 200);
omissions are explicit limitations. The CSV reader defaults to 10,000 rows/file.

RA-009 remains Stale with its last-known date/tier. RA-017 remains Missing with
no inferred tier. A missing returned assessment is unknown, not a manufactured
Missing record. Risk status is the source status at its snapshot; this layer
does not recompute risk ratings or infer remediation closure.

The existing ontology defines the stored primary SLA percentage as attainment:
actual below target is a breach. The semantic service uses this definition only
for supported percent measurements and reconciles measurement and contractual
targets. Other units/directions or conflicting targets remain unknown. Stored
breach flags are checked, not blindly trusted. `has_sla_breach` means any breach
among the returned periods, not a prediction or a latest-period-only judgment.
Truncated all-passing measurements cannot establish no breach.

Structured evidence contains source dataset, unique record/assignment ID, source
line and upstream record/file when present. Graph evidence preserves relationship
direction, source/target identities and revisions. Document citations preserve
stored references/pages; no missing page or reference is invented. Synthetic,
unreviewed or unverified evidence stays explicitly limited. Evidence returned
outside the canonical namespace is excluded. No source narrative is converted
into an automated renewal recommendation.

## Twelve deterministic questions

| Question | Service call | Expected snapshot result |
|---|---|---|
| Contracts expiring in 90 days | `get_contract_expiry_context()` | V-005, V-018, V-009, V-007, V-013, V-001 |
| V-005 Budget vs Forecast | `get_vendor_financial_context("V-005")` | USD 2,585,753.42 vs 2,880,673.98; +294,920.56 / +11.41% |
| V-009 Actual vs YTD Budget | `get_vendor_financial_context("V-009")` | USD 3,728,219.13 vs 3,328,767.09; +399,452.04 / +12.00% |
| V-001 workforce count | `get_vendor_workforce_context("V-001")` | 4 representative assignments |
| V-009 applications | `get_vendor_dependency_context("V-009")` | APP-001, APP-003, APP-008, APP-010 via SUPPORTS |
| V-001 service | `get_vendor_dependency_context("V-001")` | SVC-001 |
| V-001 SLA breach | `get_vendor_risk_sla_context("V-001")` | August 99.7984% vs 99.9% target: breach |
| V-009 risk status | `get_vendor_risk_sla_context("V-009")` | Stale; last assessment 2025-06-01, last-known High |
| V-017 assessment gap | `get_vendor_risk_sla_context("V-017")` | Missing; no assessment date or inferred tier |
| V-001 renewal context | `get_vendor_renewal_context("V-001")` | Expiry 2026-12-27, 90 days; finance, assignments, dependencies, risk/SLA and clauses |
| V-005 renewal context | `get_vendor_renewal_context("V-005")` | Expiry 2026-10-13, 15 days; same fact groups |
| V-009 dependency/workforce exposure | `get_vendor_dependency_workforce_context("V-009")` | 4 supported applications and 4 representative assignments, without financial fanout |

## Verification and approval boundaries

Run offline tests without enabling Neo4j integration tests:

```powershell
.venv\Scripts\python.exe -B -m pytest tests/test_structured_query_service.py tests/test_semantic_service.py -q -p no:cacheprovider
.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
```

Semantic test doubles use canonical source risk/performance files and current CSV
relationships. Their citation sentinel is explicitly mock evidence; they do not
validate Cypher execution. Existing query-service tests cover its transport
contract. Live validation uses injected configured graph reads only.

Before adding recommendations or agents, business owners still need to approve
renewal decision rules, acceptable variance thresholds, treatment of stale risk,
workforce extrapolation, dependency criticality, additional SLA directions/units,
and how missing or unverified evidence blocks a decision. No such thresholds or
recommendations are implemented in this phase.

Validation of this implementation: **315 passed, 2 skipped** in the full offline
suite, including **62 new tests**. The skipped tests require explicit live
integration opt-in. Read-only semantic renewal calls against Aura for V-001,
V-005, V-009 and V-017 returned matching canonical dependencies, three clauses
each, three SLA measurements each, and resolved document provenance. No mapping,
truncation or missing-provenance limitations occurred in those four samples;
synthetic/unverified evidence, separate-read consistency and expected risk gaps
remain limitations. This validates the sampled live paths, not all 20 vendors'
live results. SHA-256 checks confirm all eight structured datasets/mappings still
match their existing generation report.
