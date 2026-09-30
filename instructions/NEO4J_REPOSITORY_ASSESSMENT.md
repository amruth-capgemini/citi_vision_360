# Neo4j repository assessment and proposed implementation

Assessment date: 2026-09-28. This is a design and repository assessment, not an implemented graph loader.

## A. Current repository architecture

The checkout is a Phase 1 ontology scaffold. It does not contain the document processing or indexed-data services assumed in the task description. This is stated in [CLAUDE.md](../CLAUDE.md) and confirmed by the source inventory:

| Location | Actual contents and responsibility |
| --- | --- |
| [pyproject.toml](../pyproject.toml) | Python >=3.13, uv_build, PyYAML, jsonschema; pytest is a development dependency. No Neo4j, SQL, PDF, vector, API or LLM dependency. |
| [src/citi_project/__init__.py](../src/citi_project/__init__.py) | `main()` prints a greeting; the `citi-project` entry point does not ingest anything. |
| [models.py](../src/citi_project/services/ontology/models.py) | Frozen definition dataclasses, supported datatypes, canonical serialization, ontology exceptions. |
| [registry.py](../src/citi_project/services/ontology/registry.py) | YAML discovery, schema validation, semantic definition checks, inheritance, aliases, extraction schemas, value validation, deterministic version hash. |
| [ontology/](../ontology) | Seven domain modules plus `_schema.yaml`: 36 classes, including three abstract roots, 32 relationships, 158 directly declared class properties. |
| [tests/test_ontology_registry.py](../tests/test_ontology_registry.py) | Unit tests and optional local synthetic-pack conformance tests. No graph or ingestion fixture. |
| [backend/requirements.txt](../backend/requirements.txt) | Empty. No backend source, database lifecycle, routes, workers or migrations. |
| [README.md](../README.md) | Empty. |
| [instructions/README.md](README.md), phases 00-10 | Planned architecture. Names under `src/ingestion_platform/` are explicitly hypotheses from another walkthrough, not existing modules here. |
| [initial_plan/](../initial_plan) | Source fixtures, PDFs/Markdown, three CSVs, FSD, presentations, workbook and design notes. These are inputs and design intent, not processed semantic storage. |

The source pack is `initial_plan/Synthetic_20_Vendor_Six_Source_Data_Pack (3)/data/` (called **PACK** below). Its README and `demo_semantic_definitions.json` explicitly distinguish fixtures from a running warehouse, graph database or embedding index. The reviewed Office documents and `.copilot-tracking/dt/citi-vendor-decision-intelligence/` notes describe the intended decision-support platform. `initial_plan/var_agent_investigation_framework.md` describes a separate NetworkX investigation-policy pattern; it is not an implemented vendor graph.

The three CSVs have 10 financial rows, 690 vendor forecast rows and 20 workforce rows. Their field names and identifiers differ from PACK; no approved binding or crosswalk exists. In particular, personal workforce columns must not automatically become the pseudonymous Assignment model. Evidence: the CSV headers and [workforce.yaml](../ontology/workforce.yaml).

## B. Current end-to-end flow

```text
ontology/*.yaml + ontology/_schema.yaml
  -> OntologyRegistry.load()
  -> structural and semantic definition validation
  -> in-memory classes / properties / relations / ontology_version
  -> lookup APIs, flat-record validation and extraction-output JSON Schema

PACK operational JSON exports
  -> test-only flattening / property synonym mapping
  -> conformance assertions against OntologyRegistry

PACK PDFs + equivalent Markdown + document catalog
  -> source files only; no parser, chunk store, embedding index or graph writer
```

The test helpers `_flatten`, `_check_records` and `test_contract_records_split_into_contract_and_clauses` in [test_ontology_registry.py](../tests/test_ontology_registry.py) are not a production materializer. They do not persist entities or facts. The operational JSON exports are not claimed to have been extracted from PDFs by this repository; PACK/README.md describes them as simulated operational exports alongside simulated original documents.

The intended future flow, derived from [phases 02](02_POSTGRES_SEMANTIC_STORAGE.md), [04](04_CANONICAL_ENTITIES.md), [05](05_DOCUMENT_INGESTION.md), [06](06_CLAUSES_AND_FACTS.md), [07](07_ENTITY_RESOLUTION.md) and [08](08_NEO4J_PROJECTION.md), is:

```text
Structured sources -> reviewed ontology bindings -> canonical entities + row lineage
Documents -> retained bytes/hash -> page extraction -> chunks -> proposed classification
          -> clauses / evidence-backed facts -> entity resolution -> semantic review
Both branches -> authoritative Postgres semantic records and review history
              -> approved, version-matched Neo4j projection
```

Vector search would supply candidates, not semantic approval. YAML remains the source of truth for allowed meaning; Postgres is the planned source of truth for approved instances and history. Neo4j is rebuildable. This separation follows [instructions/README.md](README.md).

## C. Already implemented

[OntologyRegistry](../src/citi_project/services/ontology/registry.py) implements:

- Structural YAML validation and accumulated load diagnostics.
- Duplicate definition, parent/cycle, inherited override, key, endpoint, datatype, enum, pattern and synonym checks.
- Inherited class kinds, properties and keys; abstract-class instance rejection.
- Source property synonyms and case-insensitive class/relation synonym resolution.
- Subclass-aware directed relationship endpoint validation.
- Flat-record value validation and extraction-output schemas with nullable absent values; present values require confidence and chunk/page/quote evidence fields.
- A deterministic SHA-256 ontology version independent of file ordering, definition placement and formatting.

Schema validation checks the shape of evidence, not whether the chunk exists or the quote appears in the source. That work is explicitly deferred to [phase 06](06_CLAUSES_AND_FACTS.md).

Validation performed: **78 tests passed**, including local PACK conformance tests. The local virtual environment initially lacked dependencies; installed the existing project and pytest into `.venv` without changing dependency files. `uv` was unavailable in PATH, so the command used was:

```powershell
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
```

The interpreter used was Python 3.14.3. This satisfies `requires-python`, but the documented/pinned tooling target is 3.13; that interpreter remains a separate compatibility check.

## D. Missing Neo4j prerequisites and integration

Repository-wide filename/content searches, including hidden/ignored project files and planning artifacts, found no application Neo4j configuration, connection utility, driver dependency, Cypher implementation, semantic database, extracted chunk store or indexed-data adapter. No relevant Neo4j/CITI/graph/database environment variable names were present in the process, Windows user or machine environment inspected. No credential values were output. `.gitignore` contains `.env`, but no project `.env` was present.

This finding is limited to this checkout and its accessible environment. It does not establish that another deployment or checkout has no configuration.

[Phase 08](08_NEO4J_PROJECTION.md) assumes a metadata graph and approved Phase 7 records; neither is implemented here. [Phase 02](02_POSTGRES_SEMANTIC_STORAGE.md) defines the missing entities, aliases, documents, chunks, clauses, facts, relations, bindings and review-event tables, but no ORM, migration framework or database configuration has been selected in executable code.

**Implementation decision:** the ontology mapping is sufficiently clear to design. The production input/approval/provenance contract is not sufficiently implemented to connect a real ingestion pipeline. Do not create an apparent production loader that silently promotes raw fixtures to approved knowledge. Resolve whether to use another existing implementation or build the missing prerequisites here. A synthetic-only loader would be a separate explicitly labelled scope, not completion of the production objective.

## E. Proposed Neo4j model

### Business instances and definitions

Use the exact ontology class IDs as business labels. A concrete instance also receives a technical ownership label, proposed as `CitiSemantic`, and its inherited labels. Thus `Vendor` also has `LegalEntity`; `ContractDocument` also has `Document`; `RenewalClause` also has `Clause`. Abstract roots can label subclasses but cannot be instantiated directly. Do not create a `Vendor` instance merely by loading its YAML definition.

Use registry-resolved properties, datatypes, required flags, cardinalities, enums and patterns. Appendix tables enumerate every class and directed relation. They are derived from the current registry; they do not expand the ontology.

Following [phase 08](08_NEO4J_PROJECTION.md), represent definitions separately as version-scoped `OntologyClass` and `OntologyProperty` nodes with `SUBCLASS_OF`, `HAS_PROPERTY` and `INSTANCE_OF` links. Definition keys should encode `(ontology_version, class_id)` and `(ontology_version, declaring_class_id, property_id)` respectively. Inherited properties reference the original declaration. Preserve relationship definitions and their property constraints in the authoritative ontology snapshot; do not manufacture an instance edge from a relation definition. Optional FIBO alignment comes only from explicit `fibo_uri`, never inferred external classifications.

### Identity and duplicates

Use a stable canonical instance identity scoped by tenant/identity namespace, class and ontology business key, backed by the canonical entity store described in [phase 04](04_CANONICAL_ENTITIES.md). The graph receives that ID; it should not independently resolve identity. Technical graph keys may encode the canonical tuple unambiguously or use a deterministic hash of canonical JSON. Retain the components for audit and collision/conflict checks.

Do not use legal/display names to merge Vendors. `Buyer.legal_name` is an explicit exception in the ontology, requiring a reviewed crosswalk for renames. Clauses have no source key: persist their assigned identity based on contract, clause class and evidence occurrence, as noted in `_build()` in [registry.py](../src/citi_project/services/ontology/registry.py). Do not hash mutable clause values into identity. Multiple clauses of the same class must remain possible.

Equal source IDs in different namespaces must remain distinct until a reviewed crosswalk establishes equality. Exact duplicate normalized records can collapse; conflicting records for the same canonical key must fail validation or go to review, not depend on input order.

### Properties and metadata

| Ontology datatype | Proposed graph representation |
| --- | --- |
| string, text, identifier | String; preserve identifier case and source semantics. |
| integer, money_cents | Signed integer, with graph-range checks; cents never become floating point. |
| decimal, percentage | Finite numeric value; percentage bounded to 0-100. Preserve exact raw representation upstream when precision matters. |
| boolean | Boolean; reject strings and integers masquerading as booleans. |
| date | Validated native Neo4j date, with one consistent serializer. |
| year_month | Validated YYYY-MM string; do not invent a calendar day. |
| cardinality many | Homogeneous scalar list, no null elements; never a nested JSON map. |
| optional null | Property absence in Neo4j; retain missing/raw-value semantics in authoritative records. |

Use reserved technical names such as `_kg_key`, `_kg_scope`, `_kg_class_id`, `_kg_ontology_version`, `_kg_revision`, `_kg_source_ref` and `_kg_review_status`, validated against collisions. This avoids overwriting business `status` on Vendor/RiskAssessment or business `version` on Contract. Metadata names here are proposed implementation fields, not new ontology properties.

Every projected scalar must come from an approved fact or approved materialized record. Entity approval alone must not approve all extracted values. Preserve raw values, source records, fact IDs, evidence, timestamps, ontology/prompt/extractor versions and reviewer history in the authoritative store per [phase 02](02_POSTGRES_SEMANTIC_STORAGE.md).

### Relationships and provenance

All 32 business relationship IDs, directions and endpoint rules come from YAML (appendix). Canonicalize `HAS_CONTRACT` to `PARTY_TO`; do not reverse direction. Only `POTENTIAL_ALTERNATIVE_TO` currently declares a business relationship property: required `validation_status`. Do not add the proposed `PARTY_TO.role` example from phase 07 without an ontology decision.

Preserve the stable semantic assertion ID, source assertion ID, source namespace, effective dates, verification status and evidence-record references as technical assertion metadata. A source register's `Verified` is not semantic review approval. Distinct evidenced assertions between the same endpoints may coexist; rerunning the same assertion must not create another edge. If an assertion's endpoints/type change, replace its prior projection transactionally by assertion identity rather than leaving an old edge.

`EVIDENCED_BY` points from Contract/Clause/Service/SLA/RiskAssessment/PerformanceMeasurement to a Document subtype. Its declaration in [core.yaml](../ontology/core.yaml) explicitly places page, section and quote on the evidence record, not the edge. Keep durable evidence references on projected records and an independently retrievable evidence store. Do not invent `EVIDENCES` or point `EVIDENCED_BY` at an undeclared Chunk business class. Chunk projection, if later needed, must be a separately documented technical provenance layer, not an unapproved ontology addition.

Documents preserve the catalog ID and PDF/Markdown paths, expected pages and ACL roles. Index one rendition per document ID. Evidence must identify the actual retained document revision and exact chunk; PDF pages remain real, and unavailable pagination remains null. Entity-to-row provenance cannot use a new business `RECORDED_IN` relation without distinguishing the technical lineage convention in phase 08 from the YAML business vocabulary. No existing Table/Column keys can be reused because those graph nodes do not exist in this checkout.

### Proposed schema and write strategy

These are design examples, **not executed schema**:

```cypher
CREATE CONSTRAINT citi_semantic_key IF NOT EXISTS
FOR (n:CitiSemantic) REQUIRE n._kg_key IS UNIQUE;

CREATE CONSTRAINT citi_ontology_class_key IF NOT EXISTS
FOR (n:OntologyClass) REQUIRE n._kg_key IS UNIQUE;

CREATE CONSTRAINT citi_ontology_property_key IF NOT EXISTS
FOR (n:OntologyProperty) REQUIRE n._kg_key IS UNIQUE;

CREATE INDEX citi_semantic_scope_class IF NOT EXISTS
FOR (n:CitiSemantic) ON (n._kg_scope, n._kg_class_id);
```

Add per-relationship-type assertion-key uniqueness constraints if supported by the selected server/version. Verify server capabilities before migration. Uniqueness does not enforce required values, endpoint classes, disjointness or cardinality; those require preflight validation and transactional checks. Existing unrelated graph labels must never be cleared.

Values must be parameters. Labels/types may only be compiled from validated registry IDs and a fixed technical allow-list. Use `UNWIND` batches, stable-key-only node `MERGE`, then set validated properties. Match both endpoints before relationship `MERGE`; compare matched counts with requested counts so missing endpoints cannot silently disappear. Use one driver per process, explicit database selection, managed transactions, bounded retries/timeouts and deterministic transaction callbacks. Neo4j documents [MERGE and constraints](https://neo4j.com/docs/cypher-manual/current/clauses/merge/) and [managed transaction retries](https://neo4j.com/docs/python-manual/current/transactions/).

Require monotonic source revisions; an old rerun must not overwrite a newer approval. Serialize writes for the same scope or use a revision/locking protocol. Clear removed optional properties explicitly; `SET +=` alone leaves stale values when fields are omitted. An approved replacement/removal must remove its old projected assertions while preserving history upstream. Publish a complete validated snapshot atomically (or stage a generation and switch the active generation only after reconciliation). Never expose a half-written snapshot or delete existing records after a failed partial run.

### Example expected structure

```text
(Vendor V-001)-[:PARTY_TO]->(Contract CTR-001)
(Contract CTR-001)-[:HAS_SOW]->(StatementOfWork SOW-001)
(Contract CTR-001)-[:HAS_CLAUSE]->(RenewalClause <persisted clause ID>)
(Contract CTR-001)-[:EVIDENCED_BY]->(ContractDocument V-001_contract_sow)
(RenewalClause)-[:EVIDENCED_BY]->(ContractDocument V-001_contract_sow)
(StatementOfWork SOW-001)-[:FUNDS]->(Service SVC-001)
```

This is an intended shape grounded in [contracts.yaml](../ontology/contracts.yaml), [core.yaml](../ontology/core.yaml) and PACK source IDs; it is not a claim that those edges were written or approved.

## F. Recommended files/modules

Create these only after confirming the upstream boundary, preserving the existing `citi_project` package:

| Proposed file | Responsibility / repository basis |
| --- | --- |
| `src/citi_project/services/ontology/graph_projection.py` | `OntologyGraphProjector`, schema, parameterized writes, scoped rebuild; phase 08. |
| `src/citi_project/services/ontology/graph_mapping.py` | Pure registry-driven approved-record mapping and deterministic serialization; registry.py and phase 08. |
| `src/citi_project/services/ontology/graph_validation.py` | Counts, identity sets, cardinality, disjointness, evidence and revision reconciliation; phase 08. |
| `src/citi_project/services/ontology/graph_config.py` | Environment-only settings and driver lifecycle; no existing equivalent found. |
| `src/citi_project/services/ontology/graph_cli.py` | Dry-run, project, validate and explicit scoped rebuild commands, once input storage exists. |
| `tests/test_graph_mapping.py`, `test_graph_projection.py`, `test_graph_config.py` | Pure mapping/query/config tests using existing pytest conventions. |
| `tests/integration/test_neo4j_projection.py` | Opt-in isolated database tests; no live service assumed. |
| `instructions/NEO4J_INGESTION.md` | Actual runbook after command/input contract is implemented. |

Modify `pyproject.toml` and regenerate `uv.lock` to add the Neo4j driver and a dedicated CLI entry point when implementation begins. Update `README.md` with runbook links. Target registry changes narrowly for proven validation gaps and regression tests. Do not rewrite ontology definitions to accommodate source records.

Before these modules can consume production records, implement or locate the storage, binding, document/evidence, identity and review modules described in phases 02-07. Their exact ORM/migration/configuration paths must follow the selected upstream implementation rather than fabricated reuse.

## G. Step-by-step implementation and validation plan

1. Locate the expected existing pipeline/configuration, or explicitly establish this checkout as the starting point for prerequisites. Confirm identity namespace, database backend, review authority and Neo4j server/version/database. Never request credentials in chat.
2. Implement versioned semantic storage and migrations as designed in phase 02, including evidence, row lineage, approval audit and canonical IDs. Keep YAML definitions authoritative for vocabulary.
3. Implement reviewed source bindings and canonical materialization; preserve source identity crosswalks. Treat PACK as synthetic fixtures, with separate approval fixtures for tests.
4. Implement document byte retention, rendition dedup, page-aware extraction, deterministic chunking and evidence checks. Preserve exact source revisions. Do not index golden answers as business evidence.
5. Expose a narrow approved snapshot reader with entities, documents, clauses, approved scalar facts, relations and resolvable evidence. Require matching ontology version, scope, revision and review state.
6. Implement pure mapping validation before any graph side effect. Reject unknown classes/properties/types, malformed keys, missing required values, alias collisions, non-finite numbers, wrong endpoints, dangling references, invalid evidence and contradictory records. Validate cardinality across distinct endpoints rather than counting repeated evidence assertions.
7. Add schema and writer using the proposed transaction strategy. Keep logs to counts, stages and sanitized error codes; never log authentication, connection strings, raw parameters or sensitive evidence. Make exceptions actionable without echoing secrets.
8. Reconcile expected versus observed IDs and directed assertion tuples, per-class and per-type counts, property values/revisions and evidence reference sets. Validate no unapproved/mismatched-version records, duplicate identities, dangling evidence or cross-scope links. Counts alone cannot prove correct mapping.
9. Test identical replay, reordered input, duplicate records, conflicting duplicates, rename, optional-value removal, relationship replacement/revocation, older revision replay, partial failure, retry and concurrent writes. A rebuild must reproduce the approved snapshot without touching unrelated graph data.
10. Run isolated live Neo4j integration tests and manually inspect representative document/contract/clause/relationship paths. Only then publish executable ingestion commands and claim the graph layer complete.

Test matrix:

| Area | Required assertions |
| --- | --- |
| Nodes | Every concrete class maps to its exact label/key; inherited required properties preserved; abstract instances rejected; contract/document separate. |
| Relationships | All declared directions/endpoints, subclasses and synonyms; alternative validation property; disjoint SUPPORTS/USES_PORTAL; maximum cardinality. |
| Identity | Replay/order independence, namespace isolation, duplicate collapse, conflicting identity rejection, separate evidence occurrences. |
| Malformed input | Wrong types, missing keys, class ID patterns, unknown properties, alias ambiguity, NaN/infinity, out-of-range integers, absent endpoints/evidence. |
| Evidence | Real chunk/document ownership, quote containment and page consistency, one rendition, retained revision, resolvable source record. |
| Writing | Parameterization, allow-listing, rollback, retries, matched-row reconciliation, property removal, revised endpoint replacement, no unrelated deletion. |
| Configuration | Missing/blank required settings, secret-safe repr/logging/errors, unsupported URI schemes, explicit database and timeout handling. |
| Integration | Load twice, compare full identities/counts; updates, rejection/revocation, concurrent runs, rebuild parity and isolation. |

PACK source reconciliation currently gives 20 vendors, 20 contracts, 60 document IDs/PDFs with 60 equivalent Markdown renditions, 240 invoice-line rows, 283 assignments, 60 performance rows and 374 explicit source relationships (350 vendor-register + 24 shared reference). Sources: PACK files, PACK/validation_report.json and the existing conformance tests. These are **input counts**, not expected total graph counts: derived approved links, technical definition links, deduplication and projection policy change graph totals. Reconcile from the actual mapped approval snapshot.

## H. Architectural concerns and inconsistencies

1. **Contract versus document:** phase 05/08 examples incorrectly use Contract as a document class; YAML and existing tests explicitly separate ContractDocument and Contract. Some prose says EVIDENCES, but the actual declared direction is subject `EVIDENCED_BY` Document. Follow YAML.
2. **Approval absent:** source relationship `status=Verified` and source adjustment approvals are not the semantic review history required by phases 02/07. No automatic promotion is justified.
3. **Validation is incomplete at instance level:** `validate_record()` does not apply class `id_pattern`, detect conflicting synonymous fields, or enforce cross-field rules such as Missing assessment implies null risk tier. `validate_relation()` validates endpoints, not graph-wide cardinality/disjointness or relation property values. Numeric finite/range checks also need a graph boundary. Evidence: registry.py and models.py.
4. **No clause identity implementation:** `_build()` documents a future pipeline-assigned key; a source clause reference contains page/section rather than a persisted clause UUID or exact chunk evidence. Hashing only contract/class would collapse repeated clauses.
5. **Deferred nested data:** `_CONTRACT_NON_PROPERTY` and `_NON_PROPERTY_FIELDS` in tests intentionally exclude milestones, rate cards, sprints, entitlements, unit rate cards, credit bands and selected source metadata. Preserve them upstream; do not flatten them into undeclared graph properties.
6. **Projection scope versus bulk finance:** PACK/source_classes_and_agent_mapping.json directs financial facts primarily to SQL, not bulk graph/vector storage. The ontology can represent InvoiceLine and PurchaseCommitment, but their production projection policy should be explicit rather than loading every available row by default.
7. **Identity/temporal gaps:** Buyer uses name as key; historical snapshots, alias review, entitlement enforcement and cardinality across effective intervals are not implemented. Define current-snapshot versus historical projection semantics before enforcing temporal cardinality.
8. **Configuration missing:** no reusable Neo4j settings or graph service were found. Proposed future variable names are `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`; they are not existing discovered configuration. The existing ontology override is `CITI_ONTOLOGY_DIR`.
9. **Packaging/local-data assumptions:** default ontology lookup in registry.py navigates to the repository root. Installed deployments need packaged ontology resources or `CITI_ONTOLOGY_DIR`. PACK tests skip without local data. CLAUDE.md says initial_plan is ignored, but the current `.gitignore` only lists `.env`; do not assume the current ignore policy protects local source artifacts. CLAUDE.md also describes a backend virtualenv/frontend not present in this checkout.
10. **Source manifest verification:** verification of all 254 entries found 61 exact byte matches and 193 matches only after CRLF-to-LF normalization. Do not reuse catalog/manifest hashes as the actual stored-byte hash without recomputing. Preserve original bytes independently of any text normalization.

## Delivery status and commands

- Created: this assessment, including the ontology inventory below.
- Modified: no existing source, ontology, dependency or configuration files.
- Neo4j schema created: none; schema above is proposed only.
- Runtime ingestion command: none exists yet; inventing one would misrepresent project state.
- Tests: 78 passed with `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`.
- Intended normal tooling: `uv sync`, then `uv run pytest`, when uv is available.
- Environment names: existing `CITI_ONTOLOGY_DIR`; proposed `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`. No values are documented.
- Remaining decision: location of the expected upstream implementation, or confirmation that its missing prerequisites should be built in this scaffold. No ontology redesign is proposed.

## Appendix: complete ontology mapping inventory

The following tables are generated from the current registry and include inherited properties. `required` refers to a complete materialized record; extraction schemas intentionally allow missing values to remain null for review. Each concrete class maps to its exact class ID label. Abstract entries describe inheritance only. Technical projection metadata is separate from these business properties.


Ontology version at assessment: `7aefeae035750055991676316a9a7a3cdd2a42b111dfcefe1bbe8a56b024f292`.

### Application

Source: [ontology/technology.yaml](../ontology/technology.yaml). Kind: `entity`. Abstract: `false`. Parent: `none`. Key: `application_id`. ID pattern: `^APP-\d{3}$`.

A buyer application in the portfolio catalog, with a criticality rating.

Synonyms: System, Business application, Portal. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `application_id` | identifier | yes | one | Stable application key. |
| `name` | string | yes | one | Display name of the application. |
| `criticality` | string | yes | one | enum: Critical, Standard; Business criticality; Critical applications drive concentration and renewal priority. |
| `recovery_target_minutes` | integer | no | one | Application recovery time objective. |
| `recovery_point_minutes` | integer | no | one | Application recovery point objective. |

### Assignment

Source: [ontology/workforce.yaml](../ontology/workforce.yaml). Kind: `entity`. Abstract: `false`. Parent: `none`. Key: `assignment_id`. ID pattern: `^ASN-\d{3}-\d{3}$`.

One active vendor resource assignment under a statement of work, including non-billable shadow assignments.

Synonyms: Resource assignment, Vendor resource, Headcount. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `assignment_id` | identifier | yes | one | Stable assignment key. |
| `worker_alias` | string | no | one | PII; Pseudonymous worker reference; never a real name. |
| `role` | string | yes | one | Delivery role of the assignment. |
| `billable` | boolean | yes | one | False for non-billable shadow assignments. |
| `fte` | decimal | no | one | Full-time-equivalent allocation. |
| `monthly_allocated_service_fee_usd_cents` | money_cents | no | one | Monthly service fee allocated to the assignment for cost attribution. Includes overhead; not an employee salary. |
| `location` | string | no | one | Delivery region of the assignment. |
| `team_id` | identifier | no | one | Vendor delivery team key. |
| `start_date` | date | no | one | Assignment start date. |
| `end_date` | date | no | one | Assignment end date. |

### BusinessProcess

Source: [ontology/technology.yaml](../ontology/technology.yaml). Kind: `entity`. Abstract: `false`. Parent: `none`. Key: `process_id`. ID pattern: `^PROC-\d{3}$`.

A buyer business process supported by a (typically non-technical) service.

Synonyms: none. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `process_id` | identifier | yes | one | Stable business process key. |
| `name` | string | yes | one | Display name of the process. |

### Buyer

Source: [ontology/core.yaml](../ontology/core.yaml). Kind: `entity`. Abstract: `false`. Parent: `LegalEntity`. Key: `legal_name`. ID pattern: `none`.

The customer legal entity that procures from vendors (Meridian Vale Financial Group in the synthetic pack). Has no enterprise ID in the source data, so the legal name is the key.

Synonyms: Customer, Client. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `legal_name` | string | yes | one | aliases: name; Registered legal name of the entity. |

### Clause

Source: [ontology/contracts.yaml](../ontology/contracts.yaml). Kind: `clause`. Abstract: `true`. Parent: `none`. Key: `pipeline-assigned clause identity / abstract`. ID pattern: `none`.

A provision of a contract. Clause values are extracted from a ContractDocument and each present value carries chunk/page/quote evidence.

Synonyms: Provision, Term. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `section_title` | string | no | one | Heading of the document section that contains the clause. |

### ConfigurationItem

Source: [ontology/technology.yaml](../ontology/technology.yaml). Kind: `entity`. Abstract: `false`. Parent: `none`. Key: `ci_id`. ID pattern: `^CI-\d{3}-\d+$`.

A technical asset (runtime, endpoint) that a service depends on.

Synonyms: CI, Technical asset. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `ci_id` | identifier | yes | one | Stable configuration item key. |
| `ci_type` | string | yes | one | enum: Service runtime, Monitoring endpoint; aliases: type; Type of technical asset. |
| `region` | string | no | one | Hosting region of the asset. |

### Contract

Source: [ontology/contracts.yaml](../ontology/contracts.yaml). Kind: `entity`. Abstract: `false`. Parent: `none`. Key: `contract_id`. ID pattern: `^CTR-\d{3}$`.

A binding commercial agreement between the buyer and a vendor, with a term, a commercial model and clause-level obligations.

Synonyms: Agreement, Master agreement, Order form. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `contract_id` | identifier | yes | one | Stable contract key. |
| `framework_id` | identifier | no | one | Framework agreement the contract is called off from. |
| `order_id` | identifier | no | one | Order under the framework agreement. |
| `start_date` | date | yes | one | Commencement date of the current term. |
| `end_date` | date | yes | one | Expiry date of the current term. |
| `pricing_model` | string | yes | one | enum: Fixed capacity retainer, Fixed managed service fee, Fixed price with acceptance gates, Capped time and materials, Fixed price per sprint, Stage-gated fixed research budget, Named-seat subscription, Measured unit rates, Fixed price per cohort, Fixed advisory milestones; Commercial model that determines how charges accrue. |
| `document_format` | string | no | one | enum: Capacity support schedule, Managed service agreement, Fixed-price delivery SOW, Capped T&M work order, Agile delivery work order, Research stage-gate SOW, SaaS subscription order, Unit-rate service order, Training call-off order, Advisory milestone agreement; Contract family / document template. |
| `annual_base_fee_usd_cents` | money_cents | no | one | Annual commercial baseline or quantity forecast. Not a guaranteed minimum and not total multi-year contract value. |
| `approved_adjustment_usd_cents` | money_cents | no | one | aliases: approved_2025_adjustment_usd_cents; Approved uplift ceiling above the annual baseline for the reporting year. |
| `annual_ceiling_usd_cents` | money_cents | no | one | Hard annual spend ceiling for capped time-and-materials contracts. |
| `billable_assignments` | integer | no | one | Contracted count of billable workforce assignments. |
| `shadow_assignments` | integer | no | one | Contracted count of non-billable shadow assignments. |
| `scope` | text | no | one | In-scope services. |
| `out_of_scope` | text | no | one | Explicitly excluded services. |
| `billing_rule` | text | no | one | Rule that determines what charges are payable. |
| `budget_basis` | text | no | one | How the annual budget figure should be interpreted. |
| `delivery_cycle` | text | no | one | Delivery cadence within the term. |
| `format_outline` | text | no | one | Section outline of the contract family's document template. |
| `version` | string | no | one | Contract document version. |

### ContractDocument

Source: [ontology/contracts.yaml](../ontology/contracts.yaml). Kind: `document`. Abstract: `false`. Parent: `Document`. Key: `document_id`. ID pattern: `^V-\d{3}_contract_sow$`.

The original contract / SOW document (PDF with an equivalent Markdown rendition). Evidence source for contract clauses.

Synonyms: none. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `document_id` | identifier | yes | one | aliases: source_document_id, evidence_document_id; Stable document catalog key. |
| `pdf_path` | string | no | one | aliases: path; Relative path of the PDF rendition. |
| `markdown_path` | string | no | one | Relative path of the Markdown rendition. |
| `pages_expected` | integer | no | one | Page count of the PDF rendition. |
| `acl_roles` | string | no | many | Roles entitled to retrieve this document. |

### CostCenter

Source: [ontology/commercial.yaml](../ontology/commercial.yaml). Kind: `entity`. Abstract: `false`. Parent: `none`. Key: `cost_center_id`. ID pattern: `^CC-\d{2}$`.

A buyer cost center to which vendor spend is charged.

Synonyms: none. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `cost_center_id` | identifier | yes | one | Stable cost center key. |

### DataField

Source: [ontology/data.yaml](../ontology/data.yaml). Kind: `entity`. Abstract: `false`. Parent: `none`. Key: `field_id`. ID pattern: `none`.

A field (column / JSON path) within a dataset; the unit of structured binding.

Synonyms: Column, Attribute. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `field_id` | identifier | yes | one | Stable field key (dataset_id + field path). |
| `path` | string | yes | one | Column name or JSON path within the dataset. |
| `source_datatype` | string | no | one | Datatype as declared by the source. |

### Dataset

Source: [ontology/data.yaml](../ontology/data.yaml). Kind: `entity`. Abstract: `false`. Parent: `none`. Key: `dataset_id`. ID pattern: `none`.

A structured export (file or table) published by a source system at a stated grain.

Synonyms: Table, Source file. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `dataset_id` | identifier | yes | one | Stable dataset key. |
| `grain` | text | yes | one | What one record represents. |
| `as_of` | date | no | one | Snapshot date of the export. |

### Deliverable

Source: [ontology/contracts.yaml](../ontology/contracts.yaml). Kind: `entity`. Abstract: `false`. Parent: `none`. Key: `deliverable_id`. ID pattern: `^SOW-\d{3}-D\d+$`.

A contracted output with a specific acceptance test and acceptance owner.

Synonyms: none. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `deliverable_id` | identifier | yes | one | aliases: id; Stable deliverable key. |
| `output` | string | yes | one | Name of the contracted output. |
| `acceptance_test` | text | yes | one | Objective test the output must pass to be accepted. |

### DependencyRegisterDocument

Source: [ontology/technology.yaml](../ontology/technology.yaml). Kind: `document`. Abstract: `false`. Parent: `Document`. Key: `document_id`. ID pattern: `^V-\d{3}_dependency_register$`.

The original service dependency register (PDF with an equivalent Markdown rendition). Evidence source for dependency relationships.

Synonyms: none. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `document_id` | identifier | yes | one | aliases: source_document_id, evidence_document_id; Stable document catalog key. |
| `pdf_path` | string | no | one | aliases: path; Relative path of the PDF rendition. |
| `markdown_path` | string | no | one | Relative path of the Markdown rendition. |
| `pages_expected` | integer | no | one | Page count of the PDF rendition. |
| `acl_roles` | string | no | many | Roles entitled to retrieve this document. |

### Document

Source: [ontology/core.yaml](../ontology/core.yaml). Kind: `document`. Abstract: `true`. Parent: `none`. Key: `document_id`. ID pattern: `none`.

An original source document with a stable document ID. PDF and Markdown files are renditions of the same document; exactly one rendition is indexed.

Synonyms: none. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `document_id` | identifier | yes | one | aliases: source_document_id, evidence_document_id; Stable document catalog key. |
| `pdf_path` | string | no | one | aliases: path; Relative path of the PDF rendition. |
| `markdown_path` | string | no | one | Relative path of the Markdown rendition. |
| `pages_expected` | integer | no | one | Page count of the PDF rendition. |
| `acl_roles` | string | no | many | Roles entitled to retrieve this document. |

### Invoice

Source: [ontology/commercial.yaml](../ontology/commercial.yaml). Kind: `entity`. Abstract: `false`. Parent: `none`. Key: `invoice_id`. ID pattern: `^INV-\d{3}-\d{6}$`.

A vendor invoice header for one billing period. Amounts live on invoice lines.

Synonyms: Bill. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `invoice_id` | identifier | yes | one | Stable invoice key. |
| `period` | year_month | yes | one | Billing period covered by the invoice. |
| `currency` | string | yes | one | enum: USD; Billing currency. |
| `status` | string | yes | one | enum: Posted; Accounting status of the invoice. |

### InvoiceLine

Source: [ontology/commercial.yaml](../ontology/commercial.yaml). Kind: `fact`. Abstract: `false`. Parent: `none`. Key: `invoice_line_id`. ID pattern: `^INV-\d{3}-\d{6}-\d{2}$`.

One posted invoice line with its approved forecast allocation for the same vendor/month. Spend is summed once per invoice line. Variance equals actual minus forecast and reconciles to the three variance drivers.

Synonyms: none. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `invoice_line_id` | identifier | yes | one | Stable invoice line key; the grain for spend aggregation. |
| `period` | year_month | yes | one | Reporting month of the line. |
| `currency` | string | yes | one | enum: USD; Currency of all amounts on the line. |
| `actual_usd_cents` | money_cents | yes | one | Posted actual spend. |
| `forecast_usd_cents` | money_cents | yes | one | Approved forecast allocated to the same month. |
| `variance_usd_cents` | money_cents | yes | one | Actual minus forecast. |
| `volume_change_usd_cents` | money_cents | no | one | aliases: variance_drivers_usd_cents.volume_change; Variance driver attributable to volume change. |
| `rate_mix_usd_cents` | money_cents | no | one | aliases: variance_drivers_usd_cents.rate_mix; Variance driver attributable to rate or mix. |
| `scope_change_usd_cents` | money_cents | no | one | aliases: variance_drivers_usd_cents.scope_change; Variance driver attributable to scope change. |
| `forecast_version` | string | yes | one | Forecast version the allocation was taken from. |
| `adjustment_approval_id` | identifier | no | one | Change control approval that authorises a positive adjustment. |

### LegalEntity

Source: [ontology/core.yaml](../ontology/core.yaml). Kind: `entity`. Abstract: `true`. Parent: `none`. Key: `pipeline-assigned clause identity / abstract`. ID pattern: `none`.

A legally recognised party that can enter into agreements. Abstract root for the buyer, vendors and vendor parent groups.

Synonyms: none. Explicit FIBO URI: https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `legal_name` | string | yes | one | aliases: name; Registered legal name of the entity. |

### MetricDefinition

Source: [ontology/data.yaml](../ontology/data.yaml). Kind: `entity`. Abstract: `false`. Parent: `none`. Key: `metric_id`. ID pattern: `none`.

A governed business metric with an approved calculation rule and reporting period (spend, variance, concentration, renewal priority, risk freshness).

Synonyms: Metric, KPI, Measure. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `metric_id` | identifier | yes | one | Stable metric key. |
| `name` | string | yes | one | Business name of the metric. |
| `calculation` | text | yes | one | Approved calculation rule. |
| `reporting_period` | string | no | one | Period the metric is defined over. |
| `policy_version` | string | yes | one | Version of the definition catalog. |

### OrganizationUnit

Source: [ontology/core.yaml](../ontology/core.yaml). Kind: `entity`. Abstract: `false`. Parent: `none`. Key: `organization_id`. ID pattern: `^ORG-\d{2}$`.

An internal business division of the buyer that owns products, people and vendor relationships.

Synonyms: Business unit, Organization. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `organization_id` | identifier | yes | one | Stable organization key. |
| `name` | string | yes | one | Display name of the organization unit. |

### Owner

Source: [ontology/core.yaml](../ontology/core.yaml). Kind: `entity`. Abstract: `false`. Parent: `none`. Key: `owner_id`. ID pattern: `^OWN-(APP-)?\d{3}$`.

A named accountable role on the buyer side (service, application, risk or acceptance owner). Represents a role holder, not an HR record.

Synonyms: Business owner, Risk owner, Acceptance owner. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `owner_id` | identifier | yes | one | aliases: primary_owner_id, risk_owner_id, acceptance_owner_id; Stable owner key. |
| `role` | string | yes | one | Role title of the accountable owner. |
| `email` | string | no | one | PII; Contact address for the owner. |

### ParentEntity

Source: [ontology/commercial.yaml](../ontology/commercial.yaml). Kind: `entity`. Abstract: `false`. Parent: `LegalEntity`. Key: `parent_vendor_id`. ID pattern: `^PARENT-\d{2}$`.

The ultimate parent legal entity of one or more vendors. Shared parents indicate related suppliers and concentration.

Synonyms: Parent company, Holding company. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `legal_name` | string | yes | one | aliases: name; Registered legal name of the entity. |
| `parent_vendor_id` | identifier | yes | one | Stable parent group key. |

### PaymentTermsClause

Source: [ontology/contracts.yaml](../ontology/contracts.yaml). Kind: `clause`. Abstract: `false`. Parent: `Clause`. Key: `pipeline-assigned clause identity / abstract`. ID pattern: `none`.

Governs invoicing frequency, currency, schedule and payment due period.

Synonyms: Payment clause, Charges and payment. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `section_title` | string | no | one | Heading of the document section that contains the clause. |
| `payment_terms_days` | integer | yes | one | Days after invoice by which payment is due (net days). |
| `payment_schedule` | string | no | one | enum: Monthly in arrears, Monthly progress instalments; When charges are invoiced relative to delivery. |
| `invoice_frequency` | string | no | one | enum: Monthly, Quarterly, Annually; How often invoices are raised. |
| `billing_currency` | string | no | one | enum: USD; Contract billing currency. |

### PerformanceMeasurement

Source: [ontology/risk.yaml](../ontology/risk.yaml). Kind: `fact`. Abstract: `false`. Parent: `none`. Key: `metric_record_id`. ID pattern: `^MET-\d{3}-\d{2}$`.

One measured monthly result of a service's primary SLA metric, with breach flag and an estimated (unposted) service credit.

Synonyms: SLA result, Performance record. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `metric_record_id` | identifier | yes | one | Stable measurement key. |
| `period` | year_month | yes | one | Measured month. |
| `metric_name` | string | yes | one | Name of the measured metric. |
| `unit` | string | no | one | enum: percent; Unit of target and actual. |
| `target` | percentage | yes | one | Target attainment for the month. |
| `actual` | percentage | yes | one | Measured attainment for the month. |
| `eligible_units` | integer | yes | one | Denominator of the attainment formula. |
| `failed_units` | integer | yes | one | Failed units in the numerator adjustment. |
| `measurement_unit` | string | no | one | What one unit measures (minutes, alerts, service orders ...). |
| `breach` | boolean | yes | one | True when actual is below target. |
| `service_fee_basis_usd_cents` | money_cents | no | one | Monthly fee the credit percentage applies to. |
| `estimated_credit_percent` | percentage | no | one | Estimated credit percentage from the credit bands. |
| `estimated_credit_usd_cents` | money_cents | no | one | Estimated credit amount; requires claim review and is never spend. |
| `credit_status` | string | no | one | enum: Not applicable, Claim review required; Claim handling status of the estimated credit. |

### Product

Source: [ontology/core.yaml](../ontology/core.yaml). Kind: `entity`. Abstract: `false`. Parent: `none`. Key: `product_id`. ID pattern: `^PROD-\d{2}$`.

A buyer business product or product line that applications enable and spend is attributed to.

Synonyms: none. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `product_id` | identifier | yes | one | Stable product key. |
| `name` | string | yes | one | Display name of the product. |

### PurchaseCommitment

Source: [ontology/commercial.yaml](../ontology/commercial.yaml). Kind: `fact`. Abstract: `false`. Parent: `none`. Key: `purchase_order_id`. ID pattern: `^PO-\d{3}-\d{4}$`.

A forward purchase-order commitment. It is not spend and is never added to closed-year actuals.

Synonyms: Purchase order, Open PO, Commitment. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `purchase_order_id` | identifier | yes | one | Stable purchase order key. |
| `period` | year_month | yes | one | Month at which the commitment is stated. |
| `currency` | string | yes | one | enum: USD; Commitment currency. |
| `open_commitment_usd_cents` | money_cents | yes | one | Remaining committed amount. |

### RenewalClause

Source: [ontology/contracts.yaml](../ontology/contracts.yaml). Kind: `clause`. Abstract: `false`. Parent: `Clause`. Key: `pipeline-assigned clause identity / abstract`. ID pattern: `none`.

Governs whether and how the contract renews at expiry, including the notice period and the date by which notice must be given.

Synonyms: Auto-renewal clause, Evergreen clause, Renewal notice. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `section_title` | string | no | one | Heading of the document section that contains the clause. |
| `automatic_renewal` | boolean | yes | one | True when the contract renews automatically unless notice is given. |
| `renewal_extension_months` | integer | no | one | Length of each automatic renewal period in months (0 when none). |
| `notice_days` | integer | yes | one | Calendar days of notice required before expiry. |
| `notice_deadline` | date | no | one | Last date on which renewal / non-renewal notice may be given. |
| `renewal_mechanism` | text | no | one | Stated renewal mechanism wording (e.g. explicit written renewal required). |

### RiskAssessment

Source: [ontology/risk.yaml](../ontology/risk.yaml). Kind: `entity`. Abstract: `false`. Parent: `none`. Key: `assessment_id`. ID pattern: `^RA-\d{3}$`.

The latest third-party risk assessment state for a vendor service. Status may be Current, Stale (older than validity) or Missing; when Missing, risk_tier is null and must not be inferred.

Synonyms: TPRM assessment, Vendor risk assessment. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `assessment_id` | identifier | yes | one | Stable assessment key. |
| `assessment_date` | date | no | one | Date the assessment was completed; null when missing. |
| `status` | string | yes | one | enum: Current, Stale, Missing; Freshness state of the assessment. |
| `risk_tier` | string | no | one | enum: High, Medium, Low; Assessed inherent risk tier; null when the assessment is missing. |
| `validity_days` | integer | no | one | Days an assessment remains current. |
| `data_classification` | string | no | one | enum: Internal synthetic, Restricted synthetic; Classification of the data the service handles. |

### RiskIssue

Source: [ontology/risk.yaml](../ontology/risk.yaml). Kind: `entity`. Abstract: `false`. Parent: `none`. Key: `issue_id`. ID pattern: `^ISSUE-\d{3}$`.

A control finding raised in a risk assessment, with owner, due date and status.

Synonyms: Finding, Control issue. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `issue_id` | identifier | yes | one | Stable issue key. |
| `finding` | text | yes | one | Description of the finding. |
| `status` | string | yes | one | enum: Open, Closed; Issue status. |
| `severity` | string | yes | one | enum: High, Medium, Low; Issue severity. |
| `due_date` | date | no | one | Remediation due date. |
| `closed_date` | date | no | one | Date the issue was closed; null while open. |
| `control_id` | identifier | no | one | Control the finding relates to. |

### Service

Source: [ontology/technology.yaml](../ontology/technology.yaml). Kind: `entity`. Abstract: `false`. Parent: `none`. Key: `service_id`. ID pattern: `^SVC-\d{3}$`.

A service a vendor delivers to the buyer under a statement of work. The unit that supports applications or processes and is measured by SLAs.

Synonyms: Vendor service, Managed service. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `service_id` | identifier | yes | one | Stable service key. |
| `service_name` | string | yes | one | Display name of the service. |
| `recovery_time_target_minutes` | integer | no | one | Recovery time objective (RTO) for the service. |
| `recovery_point_target_minutes` | integer | no | one | Recovery point objective (RPO) for the service. |

### ServiceLevelAgreement

Source: [ontology/risk.yaml](../ontology/risk.yaml). Kind: `entity`. Abstract: `false`. Parent: `none`. Key: `sla_id`. ID pattern: `^SLA-\d{3}$`.

The contractual service-level schedule for a service: primary metric, target, measurement rules and credit regime.

Synonyms: SLA, Service level schedule. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `sla_id` | identifier | yes | one | Stable SLA key. |
| `metric` | string | yes | one | Primary measured metric. |
| `target_percent` | percentage | yes | one | Target attainment for the primary metric. |
| `formula` | text | no | one | Attainment formula. |
| `measurement_window` | string | no | one | Period over which attainment is measured. |
| `exclusions` | text | no | one | Permitted measurement exclusions. |
| `priority_1_response_minutes` | integer | no | one | Maximum response time for priority-1 incidents. |
| `priority_1_restoration_target_minutes` | integer | no | one | Target restoration time for priority-1 incidents. |
| `commercial_invoice_accuracy_target_percent` | percentage | no | one | Target invoice accuracy. |
| `monthly_credit_cap_percent` | percentage | no | one | Maximum service credit as a share of the monthly fee. |
| `claim_deadline_days_after_month_end` | integer | no | one | Days after month end by which a credit claim must be raised. |
| `credit_basis` | text | no | one | Fee basis against which credits are calculated. |

### Site

Source: [ontology/core.yaml](../ontology/core.yaml). Kind: `entity`. Abstract: `false`. Parent: `none`. Key: `site_id`. ID pattern: `^SITE-\d{2}$`.

A physical buyer facility where a vendor service is delivered.

Synonyms: Facility, Location. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `site_id` | identifier | yes | one | aliases: facility_id; Stable site key. |

### SlaRiskPackDocument

Source: [ontology/risk.yaml](../ontology/risk.yaml). Kind: `document`. Abstract: `false`. Parent: `Document`. Key: `document_id`. ID pattern: `^V-\d{3}_sla_risk_pack$`.

The original SLA and risk report pack (PDF with an equivalent Markdown rendition). Evidence source for SLA terms, measurements and assessments.

Synonyms: none. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `document_id` | identifier | yes | one | aliases: source_document_id, evidence_document_id; Stable document catalog key. |
| `pdf_path` | string | no | one | aliases: path; Relative path of the PDF rendition. |
| `markdown_path` | string | no | one | Relative path of the Markdown rendition. |
| `pages_expected` | integer | no | one | Page count of the PDF rendition. |
| `acl_roles` | string | no | many | Roles entitled to retrieve this document. |

### SourceSystem

Source: [ontology/data.yaml](../ontology/data.yaml). Kind: `entity`. Abstract: `false`. Parent: `none`. Key: `source_system_id`. ID pattern: `^SYN-[A-Z-]+$`.

An operational system of record that exports source data (CLM, ERP, WFM, TPRM ...).

Synonyms: none. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `source_system_id` | identifier | yes | one | aliases: source_system; Stable source system key. |
| `source_class` | string | no | one | enum: 01_vendor_ownership, 02_contracts_sows, 03_spend_forecast, 04_workforce, 05_service_dependencies, 06_risk_sla_performance; Source class the system feeds. |

### StatementOfWork

Source: [ontology/contracts.yaml](../ontology/contracts.yaml). Kind: `entity`. Abstract: `false`. Parent: `none`. Key: `sow_id`. ID pattern: `^SOW-\d{3}$`.

The work description under a contract that defines deliverables, acceptance and the services it funds.

Synonyms: SOW, Work order, Schedule. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `sow_id` | identifier | yes | one | Stable statement of work key. |

### TerminationClause

Source: [ontology/contracts.yaml](../ontology/contracts.yaml). Kind: `clause`. Abstract: `false`. Parent: `Clause`. Key: `pipeline-assigned clause identity / abstract`. ID pattern: `none`.

Governs termination for convenience, early-exit charges and exit / transition obligations.

Synonyms: Exit clause, Termination for convenience. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `section_title` | string | no | one | Heading of the document section that contains the clause. |
| `termination_convenience_days` | integer | no | one | Calendar days of written notice to terminate for convenience. |
| `early_exit_fee_months` | decimal | no | one | Early-exit fee expressed as a number of monthly base fees. |
| `exit_terms` | text | no | one | Transition and exit obligations wording. |

### Vendor

Source: [ontology/commercial.yaml](../ontology/commercial.yaml). Kind: `entity`. Abstract: `false`. Parent: `LegalEntity`. Key: `vendor_id`. ID pattern: `^V-\d{3}$`.

A third-party legal entity that supplies services to the buyer. Identified by a stable vendor ID; source-system IDs are recorded for identity resolution and outrank name matches.

Synonyms: Supplier, Third party, Provider. Explicit FIBO URI: none.

| Property | Type | Required | Cardinality | Constraints / aliases / sensitivity |
| --- | --- | --- | --- | --- |
| `legal_name` | string | yes | one | aliases: name; Registered legal name of the entity. |
| `vendor_id` | identifier | yes | one | Stable canonical vendor key. |
| `vendor_type` | string | yes | one | enum: Technical, Non-technical; Whether the vendor delivers technology services or non-technical services. |
| `category` | string | no | one | Procurement category of the vendor's work (e.g. Payments, Facilities). |
| `status` | string | yes | one | enum: Active, Inactive; Relationship status in the vendor master. |
| `country` | string | no | one | pattern: ^[A-Z]{2}$; ISO 3166-1 alpha-2 country of the contracting entity. |
| `aliases` | string | no | many | Alternate names and legacy supplier codes used in source systems. |
| `capabilities` | string | no | many | Declared capability keywords. Keyword overlap alone never establishes that one vendor can replace another. |
| `clm_source_id` | identifier | no | one | Vendor key in the contract lifecycle management system. |
| `erp_source_id` | identifier | no | one | aliases: source_vendor_code; Vendor key in the ERP / accounts payable system. |
| `wfm_source_id` | identifier | no | one | Vendor key in the workforce management system. |
| `tprm_source_id` | identifier | no | one | Vendor key in the third-party risk management system. |

### All directed business relationships

Endpoints admit subclasses. Cardinality names are copied from the ontology; apply them to the selected effective snapshot and distinct endpoints.

| Type | Source ? target | Cardinality | Synonyms / disjoint / business properties | Source |
| --- | --- | --- | --- | --- |
| `ACCEPTED_BY` | Deliverable ? Owner | many_to_one | none declared | [contracts.yaml](../ontology/contracts.yaml) |
| `AFFECTS` | RiskIssue ? Vendor, Service, Application | many_to_many | none declared | [risk.yaml](../ontology/risk.yaml) |
| `ASSESSES` | RiskAssessment ? Vendor, Service | many_to_one | none declared | [risk.yaml](../ontology/risk.yaml) |
| `BELONGS_TO` | Product, Owner, Vendor, Service, CostCenter ? OrganizationUnit | many_to_one | none declared | [core.yaml](../ontology/core.yaml) |
| `BILLED_UNDER` | Invoice ? Contract | many_to_one | none declared | [commercial.yaml](../ontology/commercial.yaml) |
| `CHARGED_TO` | InvoiceLine ? CostCenter | many_to_one | none declared | [commercial.yaml](../ontology/commercial.yaml) |
| `COMMITTED_UNDER` | PurchaseCommitment ? Contract | many_to_one | none declared | [commercial.yaml](../ontology/commercial.yaml) |
| `DELIVERED_AT` | Service ? Site | many_to_many | none declared | [technology.yaml](../ontology/technology.yaml) |
| `DEPENDS_ON` | Service ? ConfigurationItem | one_to_many | none declared | [technology.yaml](../ontology/technology.yaml) |
| `ENABLES` | Application ? Product | many_to_one | none declared | [technology.yaml](../ontology/technology.yaml) |
| `EVIDENCED_BY` | Contract, Clause, Service, ServiceLevelAgreement, RiskAssessment, PerformanceMeasurement ? Document | many_to_many | none declared | [core.yaml](../ontology/core.yaml) |
| `FUNDS` | StatementOfWork ? Service | one_to_many | none declared | [contracts.yaml](../ontology/contracts.yaml) |
| `HAS_CLAUSE` | Contract ? Clause | one_to_many | none declared | [contracts.yaml](../ontology/contracts.yaml) |
| `HAS_DELIVERABLE` | StatementOfWork ? Deliverable | one_to_many | none declared | [contracts.yaml](../ontology/contracts.yaml) |
| `HAS_FIELD` | Dataset ? DataField | one_to_many | none declared | [data.yaml](../ontology/data.yaml) |
| `HAS_LINE` | Invoice ? InvoiceLine | one_to_many | none declared | [commercial.yaml](../ontology/commercial.yaml) |
| `HAS_SLA` | Service ? ServiceLevelAgreement | one_to_many | none declared | [risk.yaml](../ontology/risk.yaml) |
| `HAS_SOW` | Contract ? StatementOfWork | one_to_many | none declared | [contracts.yaml](../ontology/contracts.yaml) |
| `ISSUED_BY` | Invoice ? Vendor | many_to_one | none declared | [commercial.yaml](../ontology/commercial.yaml) |
| `MEASURED_AGAINST` | PerformanceMeasurement ? ServiceLevelAgreement | many_to_one | none declared | [risk.yaml](../ontology/risk.yaml) |
| `OWNED_BY` | Vendor, Contract, Service, Application, ConfigurationItem, BusinessProcess, RiskAssessment, RiskIssue ? Owner | many_to_one | none declared | [core.yaml](../ontology/core.yaml) |
| `PARTY_TO` | Vendor, Buyer ? Contract | many_to_many | synonyms: HAS_CONTRACT | [contracts.yaml](../ontology/contracts.yaml) |
| `POTENTIAL_ALTERNATIVE_TO` | Vendor ? Vendor | many_to_many | validation_status: string; required=True; enum=('Unvalidated', 'Not assessed', 'Validated'); aliases=('alternative_capacity_status',) | [technology.yaml](../ontology/technology.yaml) |
| `PROVIDES` | Vendor ? Service | one_to_many | none declared | [technology.yaml](../ontology/technology.yaml) |
| `PUBLISHES` | SourceSystem ? Dataset | one_to_many | none declared | [data.yaml](../ontology/data.yaml) |
| `RAISED_IN` | RiskIssue ? RiskAssessment | many_to_one | none declared | [risk.yaml](../ontology/risk.yaml) |
| `STAFFED_BY` | StatementOfWork ? Assignment | one_to_many | none declared | [workforce.yaml](../ontology/workforce.yaml) |
| `SUBSIDIARY_OF` | Vendor ? ParentEntity | many_to_one | none declared | [commercial.yaml](../ontology/commercial.yaml) |
| `SUPPORTS` | Service ? Application | many_to_many | disjoint: USES_PORTAL | [technology.yaml](../ontology/technology.yaml) |
| `SUPPORTS_PROCESS` | Service ? BusinessProcess | many_to_many | none declared | [technology.yaml](../ontology/technology.yaml) |
| `USES_PORTAL` | Service ? Application | many_to_many | disjoint: SUPPORTS | [technology.yaml](../ontology/technology.yaml) |
| `WORKS_ON` | Assignment ? Application | many_to_one | none declared | [workforce.yaml](../ontology/workforce.yaml) |
