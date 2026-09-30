# Deterministic Decision Intelligence Service

This layer composes the existing structured and semantic services. It produces
review contexts, overlap candidates and hypothetical assignment counts. It does
not make final renewal/consolidation decisions, use an LLM, generate queries,
read CSVs independently, change graph data or persist scenario overlays.

## Setup and response contract

```python
from citi_project.services.structured_data import StructuredQueryService
from citi_project.services.semantic_service import VendorSemanticService
from citi_project.services.decision_intelligence import (
    DecisionIntelligenceService, DecisionPolicy,
)

structured = StructuredQueryService("initial_plan")
# graph is an existing KnowledgeGraphQueryService configured for this namespace.
semantic = VendorSemanticService(structured, graph)
decision = DecisionIntelligenceService(semantic)
result = decision.get_vendor_360("V-001")
```

The namespace must be `synthetic-pack-20260928`. With no graph argument supplied
to the semantic service, graph facts remain unknown with explicit limitations;
financial, bridge-overlap and workforce capabilities remain usable.

All calls return dictionaries with these fields:

```json
{
  "namespace": "synthetic-pack-20260928",
  "capability": "vendor_360",
  "scope": {"vendor_id": "V-001"},
  "facts": {},
  "calculations": [],
  "flags": [],
  "evidence": [],
  "assumptions": [],
  "limitations": []
}
```

Evidence and limitations retain the source service records, graph relationships
and document citations. Source retrieval completeness is distinct from business
verification. Synthetic evidence is never marked business verified. Missing,
truncated or mismatched retrieval is explicitly incomplete. Returned counts are
lower bounds when graph paths are incomplete. No missing page or citation is
invented. Unknown vendor scopes return existing missing-data limitations.

## APIs

- `get_vendor_360(vendor_id, *, as_of_date=None)` returns identity, commercial,
  financial, workforce, dependencies, SLA, risk and evidence-completeness sections.
  Evidence and limitations are common top-level fields. Commercial details retain
  source contract/document identifiers, SOW, service and clause paths.
- `get_renewal_priorities(days=90, as_of_date=None)` returns expiring contracts in
  expiry-date/contract-ID order, with context and deterministic flags per vendor.
- `get_renewal_context(vendor_id, *, days=90, as_of_date=None)` returns the same
  review context for one vendor, including vendors outside the expiry window.
- `get_vendor_dependency_risk(vendor_id)` separates dependency counts, workforce,
  SLA measurements, risk assessments and distinct issue IDs. Only source issue
  status `Open` is counted as open; no other status is silently classified.
- `get_vendor_rationalization_opportunities(*, organization_id=None)` returns
  deterministic potential-overlap pairs and a unique vendor profile map.
- `get_spend_forecast_analysis(vendor_id, year=2026)` returns existing financial
  semantics, monthly scenario profiles, allocation dimensions and same-period
  Actual-versus-Forecast comparison.
- `run_workforce_scenario(*, action="baseline", percentage=0, vendor_id=None,
  organization_id=None, country=None, market=None, worker_type=None,
  target_country=None, target_worker_type=None, assignment_ids=None)` returns
  baseline, selected baseline, hypothetical counts, affected IDs and assumptions.

The structured service gains only `list_vendor_contracts(organization_id=None)`
to enumerate its already-validated master without relying on an expiry window or
private data. Existing Ask, graph query and semantic behavior are unchanged.

Portfolio operations are bounded by `max_vendors` (default and maximum 100).
Exceeding the configured bound raises `StructuredDataError`; results are not
silently truncated. Invalid policies, scenario parameters and canonical ID
formats also raise that error. The current portfolio contains 20 vendors.

## Review policy

By default there is no high-variance or high-dependency threshold. Flags are:

| Flag | Exact rule |
|---|---|
| EXPIRING_SOON | 0 <= days to expiry <= requested window |
| SLA_BREACH | At least one returned measurement has a deterministic breach |
| STALE_RISK | At least one source assessment has status Stale |
| MISSING_RISK | At least one source assessment has status Missing |
| INCOMPLETE_EVIDENCE | Reported retrieval/data gaps, including Missing risk |
| HIGH_FORECAST_VARIANCE | Returned variance percentage > caller-supplied threshold |
| HIGH_DEPENDENCY | Returned distinct application count >= caller-supplied threshold |

For example, `DecisionPolicy(high_forecast_variance_pct="10",
high_dependency_count=4)` enables two optional review flags. These are illustrative
caller choices, not approved business rules. Variance uses the semantic service's
two-decimal percentage; dependency count includes supported apps and used portals,
which remain separately quantified. Thresholds never produce a final recommendation.
There is no weighted priority score. Default expiry date is the data snapshot
2026-09-28; a supplied date changes expiry calculations only.

## Rationalization logic

Each unordered pair is considered once. It is a potential-overlap candidate if
it has the same exact financial Product_ID, at least one shared Application_ID,
or the same exact Service_ID. Organization equality is context and can filter
the portfolio; organization alone does not qualify a pair. The application
bridge is the existing validated graph-correlated snapshot. This capability
does not perform a fresh live graph refresh. SUPPORTS and USES_PORTAL are
preserved on both sides of an overlap.

There is no fuzzy similarity or service-substitutability inference. Shared
financial allocation products are especially broad signals. For example,
V-001 and V-017 qualify through PROD-01 even though that is not evidence of
interchangeable services. The current data has 70 candidate pairs, including
10 within ORG-01. The latter covers every pair among V-001, V-005, V-009,
V-013 and V-017. V-001/V-005 also share APP-001 and APP-002.

Pair spend fields repeat vendor context and must not be summed across pairs.
The unique `facts.vendors` map is the appropriate grain for portfolio totals.
Its full-year forecast totals USD 55,228,019.17. Neither applications nor workforce
multiply financial rows, and Forecast CSV spend is not added to Financial Forecast.

## Spend interpretation

Budget/Forecast are full-year 2026. Actual is January–August; comparisons use
comparable January–August Budget or Forecast. The modeled Forecast embeds the
same January–August activity as Actual, so actual-versus-forecast YTD is zero
in this snapshot. Being above Budget does not establish being above Forecast.

Organization, Product, BCID and Tech_ID are allocation dimensions of the same
amount, not additive spend categories. Monthly scenario amounts and contract
expiry are returned as context. Rate/volume/scope causal attribution and a
counterfactual expiry effect cannot be established from these inputs.

## Workforce overlays

Source vendor/organization filters define the baseline population. Country,
market, worker type and optional explicit IDs select assignments inside that
population. Country/market/worker filters are case-insensitive; canonical IDs
are exact. Empty matches are explicit and do not imply no enterprise workforce.
Assignment IDs must be unique and inside the selected scope.

Actions are baseline, reduce or shift. Percentage is 0–100; baseline requires
zero. For shifts, provide target_country and/or target_worker_type. Targets must
be exact existing category values in the selected vendor/organization population.
Already-at-target assignments are excluded from the eligible denominator.

Affected count is `ROUND_HALF_UP(eligible distinct assignments * percentage / 100)`.
Assignments are selected in ascending Assignment_ID order. The output includes
the unrounded equivalent, requested percentage and effective percentage. This is
an auditable demonstration selection rule, not an assessment of which people
should move. A 20% reduction of four assignments gives 0.8, rounded to one,
which is an effective 25% reduction.

Reduction removes selected IDs only from the overlay; shifts change count
categories and preserve the total. `selected_resulting_counts` follows the
original selected ID cohort into its new categories. Scenarios do not chain
implicitly: each call starts from the immutable loaded baseline.

`financial_impact` is always `"unavailable"` in this POC. The source allocated
service fee is not a salary, marginal rate or avoidable-cost basis. No savings,
salary or rate assumptions are introduced. Unsupported effects include FTE,
skills, utilization, productivity, service outcomes, legal/contract feasibility,
new category creation, onshore/offshore reclassification and geographic costs.
Country shifts change country counts only; market is an input filter, not an
automatically inferred target attribute.

## Twelve demo results

All defaults below use the snapshot date 2026-09-28. Monetary amounts are USD.

| # | Question/call | Result |
|---|---|---|
| 1 | Vendor 360 V-001 | Aurelix Codeworks; CTR-001/SVC-001/SOW-001; ORG-01/PROD-01; expires 2026-12-27; Budget 9,890,410.98, Forecast 10,120,547.96; 4 assignments, 4 applications; Current/High source risk; August SLA breach |
| 2 | Renewals in 90 days | V-005 (15), V-018 (30), V-009 (45), V-007 (60), V-013 (75), V-001 (90 days); all EXPIRING_SOON; SLA_BREACH on 018/009/013/001; STALE_RISK on 009 |
| 3 | Renewal V-005 | Expires 2026-10-13; 15 days; Forecast variance +294,920.56 / +11.41%; 3 assignments, 3 supported apps; Current/High risk; no returned SLA breach; default EXPIRING_SOON flag |
| 4 | Dependency risk V-009 | 4 supported apps, 1 service, 4 representative assignments; 1 breached monthly measurement; Stale assessment dated 2025-06-01, last-known High |
| 5 | Missing for V-017 | RA-017 Missing; no inferred tier/date; 1 used portal, 0 supported apps, 2 representative assignments; incomplete risk evidence |
| 6 | Portfolio overlap | 70 potential pairs; V-001/V-005 share PROD-01, ORG-01 and APP-001/APP-002 |
| 7 | ORG-01 overlap | 10 potential pairs among V-001/005/009/013/017 |
| 8 | V-005 spend | Forecast above FY Budget by 294,920.56 / 11.41%; Actual above YTD Budget by 197,728.78 / 9.00%; Actual vs YTD Forecast 0.00 / 0.00%; causal drivers unavailable |
| 9 | V-009 Actual vs YTD Budget | 3,728,219.13 vs 3,328,767.09; +399,452.04 / +12.00% |
| 10 | India baseline | 13 representative assignments: 5 Employee, 4 Contractor, 4 Consultant |
| 11 | Reduce 20% India Contractor | 4 eligible; 0.8 rounds to 1 affected (ASN-001-002); 3 India contractors remain; portfolio 60 -> 59; effective 25%; financial impact unavailable |
| 12 | Shift selected India Contractors to Consultant | Use IDs ASN-001-002 and ASN-004-002 at 100%; contractors 4 -> 2, consultants 4 -> 6; India total 13 and portfolio total 60 unchanged; financial impact unavailable |

```python
decision.run_workforce_scenario(country="India")
decision.run_workforce_scenario(
    action="reduce", percentage=20, country="India", worker_type="Contractor",
)
decision.run_workforce_scenario(
    action="shift", percentage=100, country="India", worker_type="Contractor",
    target_worker_type="Consultant",
    assignment_ids=["ASN-001-002", "ASN-004-002"],
)
```

## Validation and remaining work

```powershell
.venv\Scripts\python.exe -B -m pytest tests/test_decision_intelligence.py -q -p no:cacheprovider
.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
```

Offline tests cover the twelve calls, exact joins, evidence preservation, optional
policy boundaries, same-period financial comparisons, distinct issue counting,
unknown/truncated graph context, stable selection, scenario immutability and
unchanged hashes of all generated datasets/mappings. Tests reuse existing
canonical graph doubles; their citation sentinels are explicitly test evidence.

Implementation validation: **40 new tests passed; full offline suite 355 passed,
2 optional integration tests skipped**. Separate read-only Aura validation passed
for V-001, V-005, V-009, V-017, V-018, V-007 and V-013 and the six-contract renewal
portfolio. Live dependency counts were complete with no reported retrieval gaps,
except the expected Missing-risk gap for V-017. The sampled open issue counts
were respectively 1, 1, 1, 1, 0, 0 and 1. Synthetic/unverified evidence limitations
remain. The validation client explicitly rejected writes; no graph mutation was
performed. All eight CSV/mapping hashes still match the generation report.

Before adding an LLM, define the routing/argument contract and approval boundaries
for explanations versus actions. Before business recommendations, approve review
thresholds, evidence acceptance, overlap qualification, scenario rounding/selection
and risk freshness rules. Cost modeling additionally needs a validated avoidable
cost basis; resource optimization needs capacity, skills and feasibility inputs.
The current capabilities provide decision context only.
