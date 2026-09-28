# VaR Agent Investigation Framework and Graph Traversal Specification

## 1. Purpose

This document describes how to build an investigation framework for an agent that detects and investigates Value at Risk (VaR) exceptions. The framework uses a graph assembled from nodes and edges defined across YAML files, loads that graph into NetworkX, and executes governed, evidence-driven investigation paths.

The implementation must provide:

- A consistent investigation lifecycle across all VaR cases
- Case-specific graph traversal based on evidence and materiality
- Reusable investigation templates
- Relationship-aware and direction-aware traversal
- Hypothesis generation, testing, and ranking
- Quantitative reconciliation of identified causes to the observed VaR movement
- Complete auditability of every node visited, edge traversed, decision made, and conclusion reached

The graph is an investigation policy and evidence map. It identifies possible investigation routes, but graph connectivity alone must never be treated as proof of causality.

---

## 2. Core Design Principle

Every case follows the same general lifecycle, but the executed graph path varies by case.

```text
Common governed lifecycle
        |
        v
Case-specific starting point
        |
        v
Mandatory validation checks
        |
        v
Evidence-driven conditional branches
        |
        v
Case-specific investigation path
        |
        v
Reconciled conclusion or escalation
```

Use the following operating model:

```text
One common investigation lifecycle
+ Reusable exception-specific templates
+ Mandatory validation steps
+ Evidence-driven conditional branches
+ Ranked graph traversal
+ Quantitative reconciliation
+ Recorded investigation paths
```

Avoid both extremes:

1. **Do not use a rigid fixed path** in which every case visits exactly the same nodes.
2. **Do not let the agent traverse freely** without phase restrictions, materiality controls, or evidence requirements.

The framework controls the phases. The selected template controls allowable routes. Evidence controls which branches open. Ranking logic controls the order in which branches are examined.

---

## 3. High-Level Architecture

```text
YAML node and edge files
        |
        v
File discovery and safe YAML parsing
        |
        v
Schema and referential-integrity validation
        |
        v
Node registry and edge resolver
        |
        v
NetworkX MultiDiGraph
        |
        v
Graph query and traversal service
        |
        v
Investigation orchestrator
        |
        +--------------------------+
        |                          |
        v                          v
Hypothesis engine          Evidence adapters
        |                  MX.3 / source datasets
        +------------+-------------+
                     |
                     v
          Reconciliation engine
                     |
                     v
       Case result and audit record
```

### 3.1 Recommended modules

```text
var_agent/
├── config/
│   ├── investigation_templates.yaml
│   ├── traversal_policies.yaml
│   ├── scoring_weights.yaml
│   └── thresholds.yaml
├── ontology/
│   ├── nodes/
│   └── edges/
├── graph/
│   ├── loader.py
│   ├── validator.py
│   ├── repository.py
│   ├── filters.py
│   └── traversal.py
├── investigation/
│   ├── context.py
│   ├── orchestrator.py
│   ├── validation.py
│   ├── decomposition.py
│   ├── ranking.py
│   ├── hypotheses.py
│   ├── evidence.py
│   ├── reconciliation.py
│   └── conclusion.py
├── adapters/
│   ├── risk_results.py
│   ├── positions.py
│   ├── market_data.py
│   ├── calculations.py
│   └── reference_data.py
├── models/
│   ├── case.py
│   ├── hypothesis.py
│   ├── evidence.py
│   └── result.py
├── audit/
│   ├── events.py
│   └── writer.py
└── tests/
```

---

## 4. Graph Model

Use `networkx.MultiDiGraph` because:

- Relationships have direction.
- Two nodes may have multiple relationships.
- Each edge needs its own attributes, provenance, and effective dates.
- Parallel edges may represent different evidence sources or relationship versions.

### 4.1 Node requirements

Every node must have a globally unique `id` and a controlled `type`.

Recommended common properties:

```yaml
id: usd_rates_var
type: RiskMetric
name: USD Rates VaR
status: ACTIVE
version: 3
effective_from: "2026-09-01"
effective_to: null
source_system: MX3
owner: Market Risk
```

Potential node types include:

- `Case`
- `RiskMetric`
- `RiskResult`
- `Limit`
- `Desk`
- `Portfolio`
- `Book`
- `Trade`
- `TradeEvent`
- `RiskFactor`
- `Curve`
- `Surface`
- `MarketObservation`
- `CalculationRun`
- `Model`
- `Configuration`
- `DataFeed`
- `DataQualityCheck`
- `Mapping`
- `Control`
- `ResolutionAction`
- `Owner`

### 4.2 Edge requirements

Every edge must contain:

```yaml
id: edge_001
source: desk_var
target: usd_rates_var
relationship: HAS_COMPONENT
status: ACTIVE
effective_from: "2026-01-01"
effective_to: null
confidence: 1.0
investigation_relevance: 0.9
source_system: ontology_catalog
```

Potential relationship types include:

```text
HAS_COMPONENT
ATTRIBUTED_TO
CONTAINS
AGGREGATES
BELONGS_TO
MEASURED_BY
GOVERNED_BY
PRODUCED_BY
CALCULATED_USING
USES_CONFIGURATION
USES_MODEL
USES_INPUT
COMPARED_TO
SENSITIVE_TO
PRICED_WITH
REPRESENTED_BY
HAS_OBSERVATION
SOURCED_FROM
VALIDATED_BY
FAILED_CHECK
CREATED_BY
AMENDED_BY
BOOKED_TO
MAPPED_TO
ROLLS_UP_TO
CHANGED_SINCE
SUBSTITUTED_BY
OWNED_BY
REMEDIATED_BY
```

### 4.3 Direction semantics

Edge direction must have fixed semantic meaning. Never cast the full graph to an undirected graph for investigation.

Examples:

```text
RiskResult PRODUCED_BY CalculationRun
CalculationRun USES_INPUT MarketData
Portfolio CONTAINS Trade
Trade SENSITIVE_TO RiskFactor
RiskFactor REPRESENTED_BY Curve
```

Use explicit reverse traversal when looking upstream. Do not reverse individual relationship meanings implicitly.

### 4.4 YAML loading strategy

The loader must use two passes:

1. Discover and register all nodes.
2. Resolve and add all edges after the complete node registry exists.

This permits edges in one YAML file to refer to nodes in another file regardless of file-read order.

The loader must preserve source provenance on every graph object:

```python
node_attributes["_source_file"] = str(path)
edge_attributes["_source_file"] = str(path)
```

### 4.5 Required validation

Fail graph compilation when any of the following occur:

- Duplicate node IDs
- Duplicate edge IDs where edge IDs are required to be unique
- Unknown edge source or target
- Unknown node type
- Unknown relationship type
- Missing mandatory attributes
- Invalid effective-date ranges
- Invalid status values
- Relationship used between disallowed node types
- Conflicting active versions
- A prohibited cycle

Report warnings, rather than necessarily failing, for:

- Isolated nodes
- Low-confidence relationships
- Missing optional ownership metadata
- Expired objects retained for historical cases
- Parallel edges whose semantics may overlap

---

## 5. Case Context

Create an immutable investigation context at case initialization. It must be passed to all graph filters and evidence queries.

```python
@dataclass(frozen=True)
class InvestigationContext:
    case_id: str
    exception_type: str
    metric_id: str
    desk_id: str
    business_date: date
    prior_business_date: date
    current_value: Decimal
    previous_value: Decimal
    warning_threshold: Decimal | None
    limit_value: Decimal | None
    currency: str
    confidence_level: Decimal
    holding_period_days: int
    legal_entity_id: str | None
    calculation_run_id: str | None
    ontology_version: str
```

The context controls:

- Starting nodes
- Business-date filtering
- Effective versions
- Desk and portfolio scope
- Legal-entity scope
- Currency and metric compatibility
- Which templates and traversal rules apply
- Evidence-query time windows

Never allow a traversal to silently cross into unrelated desks, dates, legal entities, or inactive ontology versions.

---

## 6. Investigation Lifecycle

```text
1. Initialize and classify case
2. Validate exception
3. Decompose movement
4. Rank material contributors
5. Generate hypotheses
6. Traverse cause-specific branches
7. Test and quantify hypotheses
8. Reconcile total movement
9. Conclude, remediate, or escalate
```

Each phase must return a structured result containing:

- Status
- Findings
- Evidence references
- Visited nodes
- Traversed edges
- Decisions and reasons
- Next-phase recommendations
- Errors or unresolved dependencies

---

## 7. Step 1: Initialize and Classify the Case

### 7.1 Purpose

Establish the investigation scope, identify graph starting nodes, and select the correct investigation template.

### 7.2 Algorithms

- Indexed node lookup
- Attribute filtering
- Rule-based case classification
- Template selection
- Context-filtered subgraph extraction

### 7.3 Decision flow

```text
What type of exception occurred?
|
+-- Large daily VaR movement
|   +-- Select VAR_MOVEMENT template
|
+-- Warning or limit breach
|   +-- Select LIMIT_BREACH template
|
+-- Missing or stale data
|   +-- Select DATA_QUALITY template
|
+-- Failed calculation
|   +-- Select CALCULATION_FAILURE template
|
+-- Unknown discrepancy
    +-- Select GENERAL_INVESTIGATION template
```

### 7.4 Required behavior

- Resolve the case metric to exactly one active metric definition.
- Resolve the current and prior risk results.
- Resolve the calculation runs and applicable limit.
- Select the template using deterministic rules.
- Record the selected template ID and version.
- Build a logical scope predicate. Do not copy the entire graph unless necessary.

### 7.5 Output example

```yaml
case_id: CASE-VAR-001
template_id: VAR_MOVEMENT
template_version: "1.3"
start_nodes:
  - desk_var_result_2026_09_28
scope:
  desk_id: USD_RATES
  legal_entity_id: BANK_NA
  currency: USD
  valid_at: "2026-09-28"
```

---

## 8. Step 2: Validate the Exception

### 8.1 Purpose

Determine whether the observed exception is a genuine comparable movement or is explained by a calculation, configuration, timing, scope, or data-quality issue.

### 8.2 Algorithms

- Relationship-filtered BFS
- Reverse graph traversal
- Attribute equality comparison
- Numeric tolerance comparison
- Effective-date comparison
- Set comparison for portfolio populations
- Rule-based validation

### 8.3 Allowed relationships

```text
MEASURED_BY
PRODUCED_BY
CALCULATED_USING
USES_CONFIGURATION
USES_MODEL
USES_INPUT
GOVERNED_BY
COMPARED_TO
SOURCED_FROM
VALIDATED_BY
```

### 8.4 Mandatory comparisons

Compare current and prior calculation runs for:

- Metric definition
- Confidence level
- Holding period
- Calculation methodology
- Model version
- Parameter set
- Portfolio scope
- Position count
- Limit version
- Run status
- Data completeness
- Business date
- Snapshot and valuation timestamps

### 8.5 Decision flow

```text
Are current and prior results comparable?
|
+-- No
|   |
|   +-- Configuration changed
|   |   +-- Open configuration investigation
|   |
|   +-- Portfolio scope changed
|   |   +-- Open population investigation
|   |
|   +-- Calculation failed or was incomplete
|   |   +-- Open processing investigation
|   |
|   +-- Input failed validation
|       +-- Open data-quality investigation
|
+-- Yes
    +-- Continue to movement decomposition
```

### 8.6 Exit behavior

If an invalid comparison fully explains the exception, return an operational or governance conclusion without running all downstream risk-cause branches. If it explains only part of the exception, retain it as a hypothesis and continue.

---

## 9. Step 3: Decompose the Movement

### 9.1 Purpose

Break the observed VaR movement into progressively more granular contributors.

```text
Desk VaR
  -> Risk class
  -> Portfolio
  -> Book
  -> Trade
  -> Risk factor
```

### 9.2 Algorithms

- Relationship-filtered BFS
- Depth-limited BFS
- Hierarchical aggregation
- Parent-child reconciliation
- Contribution calculation

Use BFS because the agent should evaluate all entities at the same hierarchy level before expanding deeper branches.

### 9.3 Allowed relationships

```text
HAS_COMPONENT
ATTRIBUTED_TO
CONTAINS
AGGREGATES
BELONGS_TO
```

### 9.4 Contribution calculations

For additive measures:

```python
absolute_contribution = current_component - prior_component
contribution_ratio = abs(absolute_contribution) / abs(total_movement)
```

For non-additive VaR decomposition, use the available approved attribution or incremental-VaR measure. Do not assume standalone VaR components sum directly to total VaR. Preserve the attribution method used.

### 9.5 Decision flow

```text
Which first-level component explains the movement?
|
+-- One material component
|   +-- Expand that branch first
|
+-- Multiple material components
|   +-- Keep several branches active and rank them
|
+-- No component reconciles to total
    +-- Open aggregation, mapping, or attribution investigation
```

### 9.6 Output contract

Each contributor result should contain:

```yaml
node_id: usd_rates_var
level: risk_class
current_value: 9800000
prior_value: 7700000
movement: 2100000
contribution_ratio: 0.875
attribution_method: approved_component_attribution
```

---

## 10. Step 4: Rank Material Contributors

### 10.1 Purpose

Prioritize the branches most likely to explain the exception. Do not expand every branch equally.

### 10.2 Algorithms

- Best-first search
- Priority queue
- Top-k selection
- Materiality filtering
- Anomaly scoring
- Rule-based priority overrides

### 10.3 Suggested score

```text
priority_score =
    0.40 * materiality
  + 0.20 * temporal_relevance
  + 0.15 * data_reliability
  + 0.15 * semantic_relevance
  + 0.10 * anomaly_severity
```

Weights must be external configuration with versioning. They must not be hidden only in prompts or source code.

### 10.4 Priority overrides

Retain a branch even when contribution is low if it includes:

- A critical data-quality failure
- An unauthorized model or configuration change
- A failed calculation control
- An invalid limit version
- A governance breach

### 10.5 Decision flow

```text
Does the branch exceed the materiality threshold?
|
+-- Yes
|   +-- Add to active priority queue
|
+-- No
    |
    +-- Does it contain a critical control failure?
        |
        +-- Yes -> Retain with elevated priority
        +-- No  -> Defer branch
```

### 10.6 Initial limits

```yaml
minimum_contribution_ratio: 0.05
high_priority_contribution_ratio: 0.20
maximum_active_branches: 10
maximum_traversal_depth: 6
maximum_nodes_per_case: 500
maximum_paths_per_hypothesis: 20
```

These are starting values and must be calibrated against representative cases.

---

## 11. Step 5: Generate Candidate Hypotheses

### 11.1 Purpose

Convert observed changes and graph patterns into explicit, testable explanations.

### 11.2 Algorithms

- Deterministic business rules
- Graph-pattern matching
- Temporal-change detection
- Semantic relationship matching
- Candidate scoring

### 11.3 Initial hypothesis types

```text
NEW_TRADE
AMENDED_TRADE
MATURED_OR_TERMINATED_TRADE
MARKET_CURVE_MOVEMENT
VOLATILITY_SURFACE_MOVEMENT
MISSING_MARKET_DATA
STALE_MARKET_DATA
INVALID_MARKET_DATA
MODEL_VERSION_CHANGE
PARAMETER_CHANGE
CALCULATION_CONFIGURATION_CHANGE
PORTFOLIO_MAPPING_CHANGE
INCOMPLETE_POSITION_POPULATION
CALCULATION_FAILURE
AGGREGATION_ERROR
LIMIT_VERSION_ERROR
UNEXPLAINED_MOVEMENT
```

### 11.4 Decision flow

```text
What changed between the two calculations?
|
+-- Position population changed
|   +-- Generate trade-event hypotheses
|
+-- Material market input changed
|   +-- Generate market-movement hypotheses
|
+-- Model or configuration changed
|   +-- Generate methodology hypotheses
|
+-- Data-quality check failed
|   +-- Generate data-quality hypotheses
|
+-- Mapping or hierarchy changed
|   +-- Generate aggregation hypotheses
|
+-- No obvious change
    +-- Generate broad processing and unexplained-movement hypotheses
```

### 11.5 Hypothesis model

```python
@dataclass
class Hypothesis:
    hypothesis_id: str
    hypothesis_type: str
    subject_node_id: str
    related_node_ids: list[str]
    expected_evidence: list[str]
    score: float
    status: str
    estimated_impact: Decimal | None
    confidence: float | None
    reasons: list[str]
```

A graph edge such as `SENSITIVE_TO` establishes only a possible causal mechanism. A hypothesis is not supported until dated evidence confirms that the input changed, the affected position had matching sensitivity, and the estimated impact is directionally and materially consistent with the observed movement.

---

## 12. Step 6: Traverse Cause-Specific Branches

Each hypothesis type maps to a controlled traversal policy. A policy defines:

- Allowed relationship types
- Allowed node types
- Direction for each relationship
- Maximum depth
- Required filters
- Evidence required at terminal nodes
- Stopping conditions

### 12.1 Trade-change branch

```text
VaR component
  -> ATTRIBUTED_TO Portfolio
  -> CONTAINS Trade
  -> CREATED_BY or AMENDED_BY TradeEvent
```

**Algorithms:** filtered BFS, detailed DFS, temporal filtering, current-versus-prior set difference.

**Decision flow:**

```text
Did the position population change?
|
+-- New trade
|   +-- Estimate incremental contribution
|
+-- Amended trade
|   +-- Compare before-and-after economics
|
+-- Matured or terminated trade
|   +-- Estimate removal effect
|
+-- No relevant trade event
    +-- Reject or deprioritize branch
```

### 12.2 Market-movement branch

```text
Trade or portfolio
  -> SENSITIVE_TO RiskFactor
  -> REPRESENTED_BY Curve or Surface
  -> HAS_OBSERVATION MarketObservation
```

**Algorithms:** relationship-filtered traversal, best-first search, weighted shortest path to evidence, time-series difference, sensitivity-based impact estimation.

**Decision flow:**

```text
Did a material market input change?
|
+-- No
|   +-- Deprioritize market hypothesis
|
+-- Yes
    |
    +-- Do affected positions have matching sensitivity?
        |
        +-- No -> Reject or weaken hypothesis
        |
        +-- Yes
            |
            +-- Did the input pass quality checks?
                |
                +-- Yes -> Estimate genuine market impact
                +-- No  -> Redirect to data-quality branch
```

### 12.3 Data-quality branch

```text
RiskResult
  -> USES_INPUT MarketData
  -> SOURCED_FROM DataFeed
  -> VALIDATED_BY DataQualityCheck
```

**Algorithms:** reverse lineage traversal, DFS, rule validation, freshness checks, completeness checks, missing-value detection.

**Decision flow:**

```text
Did an input fail validation?
|
+-- Missing
|   +-- Determine whether substitution was used
|
+-- Stale
|   +-- Compare timestamp with allowed tolerance
|
+-- Invalid
|   +-- Trace feed and transformation lineage
|
+-- Duplicated
|   +-- Trace ingestion and aggregation
|
+-- Valid
    +-- Return to other causal branches
```

### 12.4 Model and configuration branch

```text
RiskResult
  -> PRODUCED_BY CalculationRun
  -> USES_MODEL ModelVersion
  -> USES_CONFIGURATION ParameterSet
```

**Algorithms:** reverse traversal, version comparison, effective-date filtering, configuration diff, shortest path to changed governance objects.

**Decision flow:**

```text
Did the model or configuration change?
|
+-- Approved change
|   +-- Quantify expected impact
|
+-- Unauthorized or unexpected change
|   +-- Raise governance exception
|
+-- Same model but different parameters
|   +-- Trace parameter source and quantify difference
|
+-- No relevant change
    +-- Reject hypothesis
```

### 12.5 Mapping and hierarchy branch

```text
Trade
  -> BOOKED_TO Book
  -> MAPPED_TO Portfolio
  -> ROLLS_UP_TO Desk
```

**Algorithms:** path comparison, graph difference, reverse and forward traversal, orphan detection, duplicate detection.

**Decision flow:**

```text
Did the entity mapping change?
|
+-- Approved effective-dated change
|   +-- Quantify aggregation impact
|
+-- Incorrect or unexpected change
|   +-- Raise mapping exception
|
+-- No change
    +-- Reject hypothesis
```

---

## 13. Traversal Engine Requirements

### 13.1 Filtered BFS

Use for breadth-wise decomposition and local validation.

Required inputs:

```python
filtered_bfs(
    graph,
    sources,
    allowed_relationships,
    allowed_node_types,
    context,
    max_depth,
    node_predicate=None,
    edge_predicate=None,
)
```

The traversal must:

- Respect edge direction.
- Enforce effective dates and active versions.
- Apply context scope to every candidate node and edge.
- Yield traversal records, not just node IDs.
- Stop at the configured depth or node limit.

### 13.2 Filtered DFS

Use for detailed lineage or dependency chains after a material branch has been selected.

DFS must not be used as the initial unrestricted investigation strategy because it may follow one deep but low-value branch before evaluating more material peers.

### 13.3 Best-first search

Use a max-priority queue ordered by investigation priority.

Suggested queue item:

```python
@dataclass(order=True)
class QueueItem:
    negative_priority: float
    sequence: int
    node_id: str = field(compare=False)
    phase: str = field(compare=False)
    path: tuple = field(compare=False)
    accumulated_evidence: tuple = field(compare=False)
```

Use a stable `sequence` field so equal-priority items are deterministic.

### 13.4 Weighted shortest path

Use to find an efficient route from a hypothesis subject to required evidence. NetworkX shortest-path functions minimize cost, so convert higher relevance into lower cost.

```python
def investigation_cost(source, target, edge_data, context):
    confidence = edge_data.get("confidence", 0.5)
    relevance = edge_data.get("investigation_relevance", 0.5)
    freshness = calculate_freshness(edge_data, context)

    likelihood = (
        0.4 * confidence
        + 0.4 * relevance
        + 0.2 * freshness
    )

    return 1.0 / max(likelihood, 0.01)
```

Shortest path is a ranking mechanism, not proof of causality.

### 13.5 Reverse traversal

Use reverse traversal for upstream lineage. Prefer a graph view rather than copying:

```python
reverse_view = graph.reverse(copy=False)
```

Preserve the original edge metadata and record that the traversal was performed in reverse.

### 13.6 Graph-difference analysis

Use for current-versus-prior comparisons of:

- Trade populations
- Book and portfolio mappings
- Model and configuration versions
- Market-data dependencies
- Calculation inputs

Produce explicit sets:

```text
added_nodes
removed_nodes
changed_nodes
added_edges
removed_edges
changed_edges
```

### 13.7 Cycle handling

Cycles may be legitimate. The visited key must include investigation state, not only node ID.

```python
visited_key = (
    node_id,
    investigation_phase,
    hypothesis_type,
    business_date,
    scope_id,
)
```

Also prevent repeating the same edge within one path unless the traversal policy explicitly permits it.

### 13.8 Avoid unrestricted path enumeration

Do not run unrestricted `all_simple_paths` on the ontology. If path enumeration is required, enforce:

- Defined source and target
- Small depth cutoff
- Filtered subgraph
- Allowed relationship list
- Maximum number of returned paths
- Time budget

---

## 14. Step 7: Test and Quantify Hypotheses

### 14.1 Purpose

Classify each hypothesis as supported, partially supported, rejected, unresolved, or untestable.

### 14.2 Algorithms

- Evidence matching
- Temporal alignment
- Directional consistency checks
- Sensitivity-based impact estimation
- Incremental contribution calculation
- Counterfactual comparison
- Confidence scoring

### 14.3 Generic test flow

```text
Is the expected evidence present?
|
+-- No
|   |
|   +-- Evidence should exist
|   |   +-- Reject hypothesis
|   |
|   +-- Evidence unavailable
|       +-- Mark UNTESTABLE or escalate
|
+-- Yes
    |
    +-- Does direction match the expected effect?
        |
        +-- No -> Reject or weaken hypothesis
        |
        +-- Yes
            |
            +-- Is estimated impact material?
                |
                +-- Yes -> Mark SUPPORTED
                +-- No  -> Mark PARTIALLY_SUPPORTED or IMMATERIAL
```

### 14.4 Evidence model

```python
@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    evidence_type: str
    subject_node_id: str
    as_of_time: datetime
    source_system: str
    source_record_id: str
    observed_value: Any
    quality_status: str
    provenance: dict[str, Any]
```

### 14.5 Confidence

Confidence should reflect evidence quality, not merely graph proximity.

Suggested factors:

- Evidence completeness
- Source reliability
- Temporal alignment
- Directional consistency
- Quantitative explanatory power
- Relationship confidence
- Validation-control status

Store the score components so the final confidence is explainable.

---

## 15. Step 8: Reconcile the Total Movement

### 15.1 Purpose

Measure how much of the observed VaR movement is explained by supported causes.

### 15.2 Algorithms

- Contribution aggregation
- Residual calculation
- Attribution reconciliation
- Overlap and double-counting detection
- Confidence-weighted reporting, if approved by governance

### 15.3 Calculation

```python
observed_movement = current_value - previous_value
explained_movement = sum(non_overlapping_supported_impacts)
residual = observed_movement - explained_movement
explained_ratio = abs(explained_movement) / max(abs(observed_movement), epsilon)
```

### 15.4 Non-additivity and overlap

VaR may not be additive. The implementation must store the attribution methodology and avoid blindly summing standalone VaR values.

Potential overlap example:

- A new trade changes the position population.
- The same new trade is sensitive to the moved yield curve.
- Adding the full new-trade effect and the full market effect may double-count interaction.

Use one of the following approved approaches:

- Incremental reruns
- Factor attribution supplied by the calculation engine
- Controlled counterfactual scenarios
- Shapley-style attribution if explicitly approved
- An interaction residual reported separately

### 15.5 Decision flow

```text
How much of the movement is explained?
|
+-- At least 90%
|   +-- Proceed to conclusion
|
+-- 70% to less than 90%
|   |
|   +-- Material residual remains
|       |
|       +-- Yes -> Continue targeted traversal
|       +-- No  -> Issue qualified conclusion
|
+-- Less than 70%
    +-- Expand investigation or escalate
```

Thresholds must be configurable by exception type and governance policy.

---

## 16. Step 9: Conclude, Remediate, or Escalate

### 16.1 Algorithms

- Rule-based outcome classification
- Confidence aggregation
- Evidence-path extraction
- Resolution-ontology lookup
- Owner and escalation routing

### 16.2 Decision flow

```text
Was a root cause identified with sufficient evidence?
|
+-- Yes
|   |
|   +-- Genuine risk movement
|   |   +-- Route for risk review
|   |
|   +-- Data-quality issue
|   |   +-- Route to data owner
|   |
|   +-- Trade or booking issue
|   |   +-- Route to desk or operations
|   |
|   +-- Model issue
|   |   +-- Route to model governance
|   |
|   +-- Mapping or configuration issue
|       +-- Route to reference-data or system owner
|
+-- No
    |
    +-- Required evidence unavailable
    |   +-- Escalate with missing-evidence list
    |
    +-- Material residual remains
        +-- Escalate as unexplained movement
```

### 16.3 Required result structure

```yaml
case_id: CASE-VAR-001
template_used: VAR_MOVEMENT
template_version: "1.3"
outcome: ROOT_CAUSE_IDENTIFIED
classification: GENUINE_RISK_MOVEMENT
explained_ratio: 0.958
confidence: 0.91
primary_cause:
  type: MARKET_CURVE_MOVEMENT
  object_id: USD_10Y_CURVE
  estimated_impact: 1500000
secondary_causes:
  - type: NEW_TRADE
    object_id: TRADE_98214
    estimated_impact: 600000
unexplained_residual: 100000
visited_nodes:
  - desk_var
  - usd_rates_var
  - swaps_portfolio
  - trade_98214
  - usd_10y_curve
traversed_edges:
  - edge_id: edge_001
    relationship: HAS_COMPONENT
  - edge_id: edge_014
    relationship: ATTRIBUTED_TO
  - edge_id: edge_071
    relationship: SENSITIVE_TO
```

---

## 17. Investigation Templates

Templates define governed phases without fixing every visited node.

```yaml
investigation_templates:
  - id: VAR_MOVEMENT
    version: "1.3"
    applies_to:
      - VAR_DAILY_MOVEMENT
      - VAR_WARNING
      - VAR_LIMIT_BREACH
    phases:
      - validate_exception
      - decompose_movement
      - rank_contributors
      - generate_hypotheses
      - test_trade_changes
      - test_market_changes
      - test_data_quality
      - test_model_and_mapping_changes
      - reconcile_movement
      - conclude_or_escalate
    mandatory_checks:
      - comparable_metric_definition
      - comparable_confidence_level
      - comparable_holding_period
      - completed_calculation_runs
      - valid_market_data
      - correct_limit_version
```

A separate data-quality template may emphasize lineage:

```yaml
  - id: DATA_QUALITY
    version: "1.0"
    applies_to:
      - MISSING_MARKET_DATA
      - STALE_MARKET_DATA
      - INVALID_CURVE
      - INCOMPLETE_POSITION_SET
    phases:
      - validate_failure
      - identify_affected_input
      - trace_source_lineage
      - identify_affected_calculations
      - measure_downstream_impact
      - identify_remediation
      - conclude_or_escalate
```

---

## 18. Traversal Policy Configuration

Traversal behavior must be declarative and versioned.

```yaml
policies:
  validation:
    allowed_relationships:
      - MEASURED_BY
      - PRODUCED_BY
      - CALCULATED_USING
      - USES_CONFIGURATION
      - USES_MODEL
      - USES_INPUT
      - GOVERNED_BY
      - COMPARED_TO
      - VALIDATED_BY
    max_depth: 3
    max_nodes: 100

  decomposition:
    allowed_relationships:
      - HAS_COMPONENT
      - ATTRIBUTED_TO
      - CONTAINS
      - AGGREGATES
    max_depth: 4
    max_nodes: 250
    minimum_contribution_ratio: 0.05

  market_cause:
    allowed_relationships:
      - SENSITIVE_TO
      - PRICED_WITH
      - REPRESENTED_BY
      - HAS_OBSERVATION
      - SOURCED_FROM
      - VALIDATED_BY
    max_depth: 5
    max_nodes: 200
```

Policies should also support allowed source and target node-type pairs to prevent semantically invalid traversal.

---

## 19. Investigation Orchestrator Pseudocode

```python
def investigate_var_exception(graph, raw_case, services, config):
    context = initialize_context(raw_case, graph, services)
    audit = InvestigationAudit(context.case_id)

    template = select_template(context.exception_type, config.templates)
    audit.record_template(template.id, template.version)

    validation = validate_exception(
        graph=graph,
        context=context,
        policy=config.policies["validation"],
        services=services,
        audit=audit,
    )

    if validation.fully_explains_exception:
        return conclude_operational_issue(context, validation, audit)

    decomposition = decompose_movement(
        graph=graph,
        context=context,
        policy=config.policies["decomposition"],
        services=services,
        audit=audit,
    )

    ranked_contributors = rank_contributors(
        decomposition.contributors,
        context=context,
        scoring=config.scoring,
        limits=config.limits,
    )

    hypotheses = generate_hypotheses(
        graph=graph,
        context=context,
        validation=validation,
        contributors=ranked_contributors,
        rules=config.hypothesis_rules,
        audit=audit,
    )

    hypothesis_queue = build_priority_queue(hypotheses)
    tested_hypotheses = []

    while hypothesis_queue:
        if audit.node_count >= config.limits.maximum_nodes_per_case:
            break
        if audit.time_budget_exceeded():
            break

        hypothesis = hypothesis_queue.pop()
        result = investigate_and_test_hypothesis(
            graph=graph,
            context=context,
            hypothesis=hypothesis,
            policy=config.policy_for(hypothesis.hypothesis_type),
            services=services,
            audit=audit,
        )
        tested_hypotheses.append(result)

        reconciliation = reconcile_movement(
            context=context,
            tested_hypotheses=tested_hypotheses,
            methodology=config.reconciliation_method,
        )

        if reconciliation.explained_ratio >= config.target_explained_ratio:
            break

        new_hypotheses = generate_follow_up_hypotheses(result, graph, context)
        hypothesis_queue.extend(new_hypotheses)

    final_reconciliation = reconcile_movement(
        context=context,
        tested_hypotheses=tested_hypotheses,
        methodology=config.reconciliation_method,
    )

    return conclude_or_escalate(
        context=context,
        validation=validation,
        tested_hypotheses=tested_hypotheses,
        reconciliation=final_reconciliation,
        audit=audit,
        rules=config.outcome_rules,
    )
```

---

## 20. Auditability and Explainability

Every traversal action must produce an event.

```python
@dataclass(frozen=True)
class InvestigationEvent:
    event_id: str
    case_id: str
    timestamp: datetime
    phase: str
    action: str
    source_node_id: str | None
    target_node_id: str | None
    edge_id: str | None
    relationship: str | None
    algorithm: str
    rule_id: str | None
    inputs: dict[str, Any]
    result: dict[str, Any]
    reason: str
```

Record at least:

- Template selection
- Graph version
- Start nodes
- Applied filters
- Nodes rejected by scope
- Edges traversed
- Algorithms and parameters
- Candidate scores and score components
- Hypotheses generated
- Evidence retrieved
- Hypotheses accepted or rejected
- Estimated impacts
- Reconciliation calculations
- Stopping-condition activation
- Final routing decision

The final explanation must distinguish:

```text
STRUCTURAL: Relationship exists in ontology
OBSERVED: Dated source evidence was retrieved
COMPUTED: Impact or score was calculated
INFERRED: A hypothesis was generated
CONFIRMED: Evidence and quantification support the cause
UNRESOLVED: Evidence was insufficient or contradictory
```

---

## 21. Stopping Conditions and Safety Controls

Stop or defer a branch when:

- Maximum depth is reached.
- Maximum nodes, paths, or runtime is reached.
- The branch is outside case scope.
- The object is not effective on the business date.
- The edge type is not allowed in the current phase.
- Contribution is below threshold and no control override applies.
- Candidate score is below threshold.
- The hypothesis has been disproven.
- Required evidence is unavailable.
- The branch duplicates an already tested hypothesis.
- The target explained ratio has been achieved.

Example configuration:

```yaml
limits:
  maximum_depth: 6
  maximum_nodes_per_case: 500
  maximum_paths_per_hypothesis: 20
  maximum_runtime_seconds: 120
  minimum_candidate_score: 0.30
  minimum_contribution_ratio: 0.05
  target_explained_ratio: 0.90
```

A limit being reached must never be presented as a successful conclusion. Record it as a partial investigation and include the unvisited or unresolved scope.

---

## 22. Error Handling

Define explicit exceptions and outcomes for:

- Missing start node
- Ambiguous start node
- Malformed YAML
- Invalid graph schema
- Broken edge reference
- Missing evidence adapter
- Source-system timeout
- Incompatible metric definitions
- No path to required evidence
- Numeric attribution failure
- Reconciliation overlap
- Runtime or node-budget exhaustion

Errors that invalidate the graph should fail deployment or graph loading. Case-specific missing data should produce an actionable investigation status, not crash the process.

---

## 23. Testing Strategy

### 23.1 Unit tests

Test:

- YAML parsing and two-pass graph loading
- Duplicate ID detection
- Referential-integrity checks
- Effective-date filtering
- Relationship filtering
- Direction handling
- BFS depth behavior
- DFS cycle prevention
- Priority scoring
- Stable queue ordering
- Shortest-path cost calculation
- Graph differences
- Hypothesis transitions
- Reconciliation and residual calculations
- Audit event creation

### 23.2 Synthetic scenario tests

At minimum, create scenarios for:

1. Genuine yield-curve-driven VaR increase
2. New trade plus market movement
3. Same positions but changed model version
4. Missing market observation with prior-day substitution
5. Stale volatility surface
6. Incomplete position population
7. Incorrect portfolio mapping
8. Failed or incomplete calculation run
9. Multiple simultaneous causes
10. No supported cause and material unexplained residual

### 23.3 Invariants

Assert the following:

- The same input graph, configuration, and case produce the same result.
- Disallowed relationship types are never traversed.
- Out-of-scope nodes are never used as evidence.
- Every supported hypothesis has evidence.
- Every final cause has an auditable graph path.
- Every numeric impact records its methodology.
- Reconciliation flags overlapping contributions.
- Expired ontology objects are not used for a current case.
- Runtime limits produce partial or escalated outcomes, not false success.

### 23.4 Golden-case tests

Create approved cases with expected:

- Template selection
- Mandatory path
- Optional branches
- Primary and secondary causes
- Explained ratio range
- Final classification
- Escalation owner

Use these as regression tests after ontology, rule, or scoring changes.

---

## 24. Observability

Emit metrics for:

- Cases by exception type
- Average nodes visited per phase
- Average edges traversed per phase
- Investigation runtime
- Hypotheses generated and tested
- Supported-hypothesis rate
- Explained-ratio distribution
- Escalation rate
- Missing-evidence rate
- Traversal-limit activation rate
- Most frequently used relationships
- Most common root-cause categories

Log graph version, template version, policy version, and scoring version for every case.

---

## 25. Performance Guidance

NetworkX is appropriate for a working demonstration and moderate graph sizes. To keep traversal efficient:

- Build indices by node type, business key, status, and effective date.
- Filter before traversal where possible.
- Use graph views instead of full graph copies.
- Cache immutable ontology queries by graph version.
- Keep live observations outside the static ontology when appropriate and retrieve them through adapters.
- Avoid all-pairs path algorithms for case investigations.
- Apply strict depth, node, path, and time limits.

If graph size, concurrency, or persistent path-query needs outgrow NetworkX, retain the same investigation interfaces and move graph storage and querying behind the repository abstraction to a graph database.

---

## 26. Implementation Sequence

Build the framework in the following order:

1. Define controlled node and relationship vocabularies.
2. Define YAML schemas and validation rules.
3. Build the two-pass YAML-to-NetworkX loader.
4. Add graph indices and effective-date filtering.
5. Implement audit events and deterministic traversal records.
6. Implement filtered BFS and reverse traversal.
7. Implement contribution decomposition and ranking.
8. Implement hypothesis types and deterministic generation rules.
9. Implement cause-specific traversal policies.
10. Implement evidence adapter interfaces.
11. Implement hypothesis testing and impact estimation.
12. Implement reconciliation with overlap controls.
13. Implement conclusion and escalation rules.
14. Add synthetic and golden-case tests.
15. Add observability, performance limits, and version tracking.

---

## 27. Acceptance Criteria

The implementation is complete when it can:

- Load nodes and edges from multiple YAML files into one validated directed multigraph.
- Preserve provenance from every graph object to its YAML definition.
- Select an investigation template deterministically from a case.
- Execute mandatory validation before cause analysis.
- Decompose VaR movement through permitted hierarchy relationships.
- Rank material contributors using configurable scoring.
- Generate explicit hypotheses from graph patterns and observed changes.
- Traverse only relationships allowed for the current phase and hypothesis.
- Retrieve dated evidence through defined adapters.
- Mark hypotheses as supported, partially supported, rejected, unresolved, or untestable.
- Quantify supported causes using an approved attribution method.
- Detect potential double-counting during reconciliation.
- Stop safely based on governed limits.
- Produce a final result with an explained ratio, residual, confidence, root cause, and owner.
- Reconstruct the complete investigation path from audit records.
- Produce deterministic results for identical inputs and versions.

---

## 28. Algorithm Summary

- **Indexed lookup and filtering:** initialize the case and establish scope.
- **Rule-based classification:** select the governed investigation template.
- **Filtered BFS:** validate nearby dependencies and decompose hierarchy levels.
- **Filtered DFS:** inspect a selected dependency or lineage chain deeply.
- **Reverse traversal:** trace results to runs, inputs, feeds, models, and configurations.
- **Best-first search:** investigate the highest-value candidate branch first.
- **Weighted Dijkstra:** rank efficient paths from a hypothesis to relevant evidence.
- **Graph-difference analysis:** identify population, mapping, and version changes.
- **Pattern matching:** generate candidate hypotheses.
- **Evidence matching and estimation:** test whether a candidate cause is supported.
- **Contribution reconciliation:** measure total explained movement and residual.
- **Rule-based conclusion:** classify, route, remediate, or escalate the case.

---

## 29. Overall Decision Flow

```text
Exception detected
        |
        v
Select investigation template
        |
        v
Validate calculation and comparability
        |
        +-- Invalid comparison or failed run
        |       |
        |       v
        |   Investigate operational cause
        |       |
        |       v
        |   Conclude or escalate
        |
        +-- Valid exception
                |
                v
        Decompose movement
                |
                v
        Rank material contributors
                |
                v
        Generate hypotheses
                |
        +-------+--------+---------+----------------+
        |                |         |                |
        v                v         v                v
   Trade change     Market move  Data quality  Model or mapping
        |                |         |                |
        +-------+--------+---------+----------------+
                |
                v
        Test and quantify causes
                |
                v
        Reconcile total movement
                |
                v
   Is the required percentage explained?
        |
        +-- Yes -------> Conclude and route
        |
        +-- Partially -> Continue targeted traversal or qualify
        |
        +-- No --------> Expand investigation or escalate
```

---

## 30. Final Implementation Principle

The coding agent must treat the graph as a governed map of valid investigation routes, not as an autonomous proof engine.

A defensible conclusion requires all of the following:

1. A valid graph path connecting the exception to a candidate cause
2. Dated and scoped evidence supporting the path
3. Directional consistency between the evidence and the VaR movement
4. A quantitative impact estimate or approved attribution
5. Reconciliation against the total observed movement
6. A recorded confidence level and unresolved residual
7. A complete audit trail showing why each branch was opened, deferred, rejected, or accepted

The result should be flexible enough to follow different paths for different cases, while remaining deterministic, governed, testable, and auditable.
