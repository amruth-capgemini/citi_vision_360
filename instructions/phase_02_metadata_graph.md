# Phase 2: Metadata harvesting into the knowledge graph

> Index: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md). The invariants and
> protected results in index §3 apply to this phase.

**Goal:** during ingestion, harvest business and column metadata from Postgres
into Neo4j, so agents can find which source holds which information. Link
datasets to the contracts and vendors whose records they contain.

**Prerequisites:**
- [Phase 1](phase_01_postgres.md) is complete, so Postgres is loaded.
- The Neo4j environment variables are set.
- The Azure OpenAI adapter from [Phase 0](phase_00_routing_fix.md) exists.

## Extraction specification

### Stage split

| Stage | Reads | Writes |
|---|---|---|
| Phase 1 (load) | All 8 CSVs, all rows | **All rows into Postgres.** The facts live here. |
| Phase 2 (harvest) | Postgres catalog, column statistics, a small sample | **Only metadata into Neo4j.** No fact rows. |

### Dataset level: one `Dataset` node per table

| Item | Source |
|---|---|
| Physical name (`schema.table`), source system | Postgres catalog |
| Row count, primary key | Catalog |
| Description | Table comment (loaded from `schema_lineage.json`) |
| Grain | Classifier, otherwise the LLM |
| Domain (`BELONGS_TO`) | Classifier, otherwise the LLM |
| Summary, `review_state` | LLM (`unreviewed`) |

### Column level: one `DataField` node per column

- **Profile:** type, null %, distinct count, min/max, length and value pattern
  (for example `^V-\d{3}$`).
- **Classification:** semantic role (identifier, measure, date, dimension or
  text), ontology class and property, description, confidence and `review_state`.

### Relationships

- `PUBLISHES` (system → dataset) and `HAS_FIELD` (dataset → column).
- `JOINS_TO`: two columns with the same name whose values overlap.
- `HAS_RECORDS_FOR`: dataset → Contract or Vendor anchor, only for IDs that
  actually appear in the mapped identifier column.

### What is and is not stored

- Sample rows (`--sample-rows`, default 10) are used **in memory** to help the LLM
  describe columns. They are **never stored**.
- Only two kinds of values go into the graph:
  - identifier values needed for `HAS_RECORDS_FOR` links
  - small category value sets, with at most 20 distinct values (for example
    Scenario = Budget/Forecast/Actual)
- Free text, measures and person-level fields are never stored.

### Case-collision rule

Columns whose names differ only by case, such as `Vendor_Name` and
`VENDOR_NAME` in `workforce.ct_workforce_organization`, are flagged
`name_collision`. They are never merged, and never auto-mapped at confidence 1.0.

### Worked example: `finance.ct_technology_financials`

This table has 60 rows (20 contracts × Budget/Forecast/Actual) and 84 columns.
- **Dataset:** system `PG-FINANCE` (simulating Coupa/Ariba), PK `record_id`, grain
  "one row per contract × scenario × year", domain `spend_forecast`.
- **Columns:**

  | Column | Profile | Role | Mapping |
  |---|---|---|---|
  | `Vendor_ID` | 0% null, 20 distinct, `^V-\d{3}$` | identifier | `Vendor.vendor_id` (confidence 1.0) |
  | `Contract_ID` | 20 distinct, `^CTR-\d{3}$` | identifier | `Contract.contract_id` (confidence 1.0) |
  | `Contract_End_Date` | min/max dates | date | `Contract.end_date` |
  | `Scenario` | Actual, Budget, Forecast | dimension | none; LLM describes it; value set stored |
  | `Amount`, `Jan_USD`…`Dec_USD` | numeric, min/max | measure | LLM: "USD; YTD for Actual, FY for Budget/Forecast" |
  | `Risk_Tier` | High, Medium, Low, blank | dimension | `RiskAssessment.risk_tier` |
  | `Scenario_Note` | length only | text | none; values not stored |

- **Relationships:**
  - `JOINS_TO` `clm.canonical_vendor_master.Contract_ID`
  - `HAS_RECORDS_FOR` the 20 Contract anchors

## Tasks

1. **Ontology** (`ontology/data.yaml`). Reuse `SourceSystem`, `Dataset`,
   `DataField`, `MetricDefinition`, `PUBLISHES` and `HAS_FIELD`, and add:
   - class `BusinessDomain`, keyed by `domain_id`, one per source class:
     vendor_ownership, contracts_sows, spend_forecast, workforce,
     service_dependencies, risk_sla_performance
   - Dataset properties: `summary`, `row_count`, `refreshed_at`, `physical_name`,
     `review_state`
   - DataField properties: `description`, `semantic_role`, `ontology_class`,
     `ontology_property`, `null_pct`, `distinct_count`, `value_set`,
     `name_collision`, `confidence`, `review_state`
   - relations `Dataset BELONGS_TO BusinessDomain`, `DataField JOINS_TO DataField`,
     `Dataset HAS_RECORDS_FOR Contract` and `Dataset HAS_RECORDS_FOR Vendor`
   - relax the `SourceSystem.id_pattern` if needed to allow `PG-<SCHEMA>`
2. **Namespace.** An ontology change changes `ontology_version`, and ingestion pins
   each namespace to one version. So the catalog goes into a **new namespace,
   `catalog-synthetic-pack-20260928`**. It contains the metadata nodes plus thin
   Vendor and Contract **anchor** nodes (keys and names only) built from
   `clm.canonical_vendor_master`. The existing business namespace stays readable,
   because the query service does not check the version.
3. New `src/citi_project/services/catalog/`:
   - `harvester.py` (read-only, `citi_reader`):
     - reads `information_schema` and `pg_catalog` for schemas, tables, columns,
       types, PK/FK, comments and row counts
     - profiles each column according to the extraction specification
     - takes a bounded sample of `--sample-rows` rows (default 10), with masking
       hooks for sensitive columns
     - outputs a plain `HarvestResult` dataclass
   - `classifier.py` (deterministic, runs first):
     - exact or synonym matches between column names and ontology property IDs
       (`OntologyRegistry` synonyms)
     - `*_ID` columns whose value patterns match an ontology `id_pattern` become
       identifiers
     - equal column names with overlapping values across tables become
       `JOINS_TO` candidates
     - case collisions are flagged
     - matches get `confidence=1.0` and `review_state="auto"`
   - `enricher.py` (LLM; only for what the classifier leaves unresolved):
     - one strict-JSON call per table, using the Phase 0 `AzureOpenAIJsonModel`,
       proposing domain, summary, grain, column descriptions, semantic roles and
       ontology mappings
     - the schema **enums are built from the registry**, so the model cannot
       invent classes or properties
     - results get `review_state="unreviewed"` and the model's confidence
     - `--no-llm` skips this step
   - `builder.py`:
     - builds a `GraphPayload` from `SourceSystem → Dataset → DataField`, domains,
       joins and anchors
     - adds `HAS_RECORDS_FOR` edges only to anchors whose IDs actually appear in
       the mapped identifier column. **This is the contract link.**
     - ingests through the existing `KnowledgeGraphService` (validation,
       idempotence, rollback)
     - provenance records the harvest run ID
   - `query.py`: fixed, parameterized, namespace-filtered Cypher:
     - `find_sources(concepts, domain=None)` returns dataset, system, fields,
       freshness, review state and row count
     - `describe_dataset(dataset_id)`
     - `datasets_for_contract(contract_id)`
   - `cli.py`: the `citi-catalog harvest [--dry-run] [--no-llm] [--sample-rows N]`
     and `citi-catalog describe` scripts

## Tests

- Offline:
  - the harvester with fake catalog and profile rows
  - classifier mappings (for example `Contract_End_Date` → `Contract.end_date`)
  - the `Vendor_Name` / `VENDOR_NAME` collision is flagged and not auto-mapped
  - the enricher with scripted model outputs, where invalid enums are rejected
  - `GraphMapper` accepts the builder payload
  - anchors link only to IDs that are present
  - no sample-row values appear in the payload, beyond identifiers and value sets
- Opt-in: harvest the local Postgres into the Aura catalog namespace (dry run,
  then write), and check the resulting counts.

## Status

**Gate passed (2026-09-30), with foreign-key discovery, business concepts and model
enrichment.** Namespace `catalog-synthetic-pack-20260928` on Aura, catalog ontology
version `1b4cc500...`.

Counts:
- 414 nodes: 5 systems, 8 datasets, 313 fields, 6 domains, 42 concepts, 20
  vendor anchors and 20 contract anchors
- 841 relationships, including:
  - 55 `REFERENCES` (40 auto, 15 unreviewed)
  - 23 `JOINABLE_WITH`
  - 116 `MAPS_TO`
  - 280 `HAS_RECORDS_FOR`
- The namespace validates, and all 8 tables were enriched with no failed batches.

`describe --join-plan Contract.end_date`:
- The end date is held in 4 datasets. Forecast carries it twice: `Contract_End_Date`,
  and `Term_End` through an unreviewed model mapping.
- The key home is `clm.canonical_vendor_master.Contract_ID`.
- Nine key fields in seven datasets reference it, each with its dataset's grain. Two of
  them have different names: `finance.ct_vendor_technology_forecast.Contract_Number`
  and both `Source_Record_ID` fields.

Tests:
- Offline: 512 passed and 19 skipped.
- Live Postgres: the harvest parity and Phase 1 tests pass.
- The business namespace `synthetic-pack-20260928` is untouched and still validates.

**Decision (user, 2026-09-30):** the model never sees sample rows.
`--sample-rows` defaults to 0, so the model gets only column profiles: names,
comments, types, statistics, patterns and value sets of non-sensitive categories.

## Extension: business concepts and inferred foreign keys

**Why:** agents must follow a contract across tables, even when each system names
its key differently.

**Concepts**
- One `Concept` node per ontology property in use, for example
  `Contract.end_date` with the business name "Contract end date".
- Each field links to its concept with `MAPS_TO` (basis `classifier` or `llm`).
- Every field has a `business_name`:
  - the concept's name when the classifier mapped it
  - the model's proposal otherwise
  - a humanized column name as the last fallback

**`REFERENCES` (DataField → DataField)**
- An inferred foreign key, found by value inclusion, whatever the column names.
- The identifier fields are grouped by concept, or else by value pattern.
- The group's **home key** is chosen among fields that are unique in their
  dataset (`relationships.py`), ranked by:
  1. the column alone is the primary key
  2. the dataset is a reference table, not a fact table (no measure fields)
  3. the dataset is in the entity's own domain
  4. the dataset holds more properties of the entity
  5. the dataset is narrower
- Every other field in the group whose values are 100% contained in the home key
  references it:

  | Evidence | Confidence | Review state |
  |---|---|---|
  | Same classifier concept on both sides | 1.0 | `auto` |
  | A model-proposed concept on either side | 0.9 | `unreviewed` |
  | Same value pattern only (for example `BCID`) | 0.8 | `unreviewed` |

- Masked, truncated and single-valued fields never take part. `Year`, `Scenario`
  and `Graph_Namespace` never become keys.

**`JOINABLE_WITH` (Dataset → Dataset)**
- Derived from `REFERENCES`, listing the keys (`child -> key`), so the Browser
  shows the tables as linked.

**Identifier detection by values**
- A column whose values all match one ontology ID pattern is an identifier with
  that class's key, whatever its name. Examples: `Contract_Number` →
  `Contract.contract_id`, and `Invoice_PO` / `VENDOR_PURCHASE_ORDER` →
  `PurchaseCommitment.purchase_order_id`.

**`CatalogQueryService.join_plan(concept)`**
- Returns the concept's carriers, the home of its class key, and every joining
  field with its dataset grain and review state.
- Phase 3 uses it to fetch and aggregate each dataset separately.

**Enricher**
- Now also proposes business names, and receives column comments. The comments
  fixed `Col_2026_YTP`, which is now "Remaining 2026 forecast".
- Proposals below 0.6 confidence are not recorded as mappings.
- Enrichment runs 25 columns per call with a 4,096-token output bound, through
  `select_model(max_output_tokens=..., timeout_seconds=...)`.
- If a model repeats a column, the first proposal is kept. A failed batch loses
  only that batch.

### Deviations from the tasks above, and why

1. **A separate catalog ontology**, `ontology/catalog/catalog.yaml`, instead of
   editing `ontology/data.yaml`.
   - Editing `data.yaml` changes the business `ontology_version`. That breaks the
     checked-in representative fixture.
   - It also blocks any re-ingest of the live business namespace
     `synthetic-pack-20260928`, and the decision services require that exact name.
   - The catalog registry is loaded with
     `OntologyRegistry.load(dir, schema_path=...)`. It has its own version, and
     the business version stays unchanged.
   - Its `SourceSystem`, `Dataset` and `DataField` extend the business
     definitions, and a test keeps them compatible.
2. **`VendorAnchor` and `ContractAnchor` classes** instead of `Vendor` and `Contract`.
   - The business classes require `vendor_type`, `status` and `pricing_model`,
     which Postgres does not hold, and the catalog must not invent them.
   - The anchor classes have no class-level `id_pattern`, so
     `classify_identifier("V-001")` stays unambiguous.
   - `ANCHOR_PARTY_TO` (VendorAnchor → ContractAnchor) connects each contract to
     its vendor.
3. **`IN_DOMAIN`** (with `basis`: source_system, fields or llm) instead of
   reusing `BELONGS_TO`. Reusing it would widen that relation's business
   endpoints.
4. **Dataset `as_of`** (from a single-valued `As_Of_Date`) is the freshness
   field, instead of `refreshed_at`. A wall-clock timestamp would change every
   node on every harvest.
5. Properties added:
   - Dataset: `description`, `primary_key`
   - DataField: `position`, `value_pattern`, `masked`
6. `min_value` and `max_value` are stored for date fields only. Measures and text
   keep no values.
7. The loader lineage columns (`_source_line`, `_source_file`) are not catalog
   fields.
8. **`REFERENCES` replaces `JOINS_TO`.** `JOINS_TO` needed identical column names on
   both sides. Real systems name the same key differently, so links are now found
   from values (see "Extension" below).
9. **Each harvest replaces the namespace atomically.** The catalog is derived
   data, and ingestion only adds. A changed mapping would otherwise leave a
   second `MAPS_TO` edge, and a removed column would stay behind.
   `KnowledgeGraphService.replace(payload, namespace_prefix="catalog-")` clears
   and re-ingests in one transaction, with the same validation and read-back as
   `ingest`, and re-pins the ontology version. An identical harvest still writes
   nothing.
10. **Metadata classes are never mapping targets.** The business ontology's
    `data` module (`Dataset`, `DataField`, `SourceSystem`, `MetricDefinition`)
    describes tables, not business facts, so both the classifier and the model
    ignore it.

### As built (`services/catalog/`)

- **Harvester:**
  - `harvest(source, schemas, sample_rows=0)` profiles null %, distinct count,
    lengths, numeric and date shape, and uniqueness (`ColumnProfile.unique`)
  - distinct values within the canonical namespace, kept in memory only
  - `PostgresCatalogSource` runs as `citi_reader` with quoted identifiers, one
    aggregate query per 100 columns and `COLLATE "C"`
- **Classifier:** deterministic mappings at confidence 1.0, in these cases:
  - class-qualified names
  - unique multi-word property names
  - values that all match one ID pattern
  - the column's values must also fit the property
  Also: collisions get 0.5 and `unreviewed`; person-level columns are masked;
  concept business names.
- **Relationships:** `discover_references`, `dataset_links`.
- **Enricher:** model names, descriptions, domains and mappings for unresolved
  columns, with enums built from the business registry, excluding metadata
  classes. Always `unreviewed`, with confidence between 0.6 and 0.95.
- **Builder:** a deterministic payload (run ID `harvest-<content hash>`) with
  concepts, `MAPS_TO`, `REFERENCES`, `JOINABLE_WITH` and anchors.
  `HAS_RECORDS_FOR` covers only anchor keys present in auto-mapped
  `Vendor_ID`/`Contract_ID` fields.
- **Query:** `find_sources`, `join_plan`, `describe_dataset`,
  `datasets_for_contract` and `overview`. Each is fixed, parameterized,
  namespace-filtered Cypher.
- **CLI:** `citi-catalog init-schema | harvest [--dry-run] [--no-llm] [--sample-rows N] [--output F] | describe [--dataset D | --contract C | --join-plan CONCEPT] | reset --yes`

## Gate

- The catalog namespace validates, and contains 5 systems, 8 datasets, every
  column, 6 domains and 20 linked contracts.
- `citi-catalog describe` shows the mappings and their review states.
- `describe --join-plan Contract.end_date` reaches every contract-keyed dataset,
  including `Contract_Number`.

```powershell
# The CLIs load backend/.env (CITI_PG_READER_DSN, NEO4J_*, AZURE_OPENAI_*); pytest needs --env-file .env.
uv sync --extra agents                              # openai SDK for enrichment
uv run citi-catalog harvest --dry-run --no-llm      # offline payload check, no Neo4j
uv run citi-catalog init-schema                     # catalog constraints and indexes
uv run citi-catalog harvest                         # replace the catalog (model: profiles only)
uv run citi-catalog describe --join-plan Contract.end_date
uv run pytest tests/integration/test_catalog_live.py --pg-integration --neo4j-integration -q -rs
```
