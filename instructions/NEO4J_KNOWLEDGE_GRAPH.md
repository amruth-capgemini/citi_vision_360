# Neo4j Knowledge Graph infrastructure

This feature consumes structured ontology instances. It does not parse PDFs, extract facts,
infer identity, approve evidence, or create a Postgres dependency. The original repository
assessment is historical; this runbook describes the implemented graph layer.

## Architecture

```text
Future extraction / other upstream caller
  -> GraphPayload (dataclasses or strict JSON)
  -> OntologyRegistry + GraphMapper + centralized identity
  -> namespace lock + validation of existing and proposed graph
  -> parameterized UNWIND / MERGE in one managed transaction
  -> read-back reconciliation -> commit

Read-only validation -> structured ValidationReport
```

Code lives in `src/citi_project/services/knowledge_graph/`:

| Module | Responsibility |
| --- | --- |
| `models.py` | Typed entities, references, relationships, evidence envelope and payload decoding. |
| `identity.py` | One identity algorithm for all callers, nodes and relationships. |
| `mapping.py` | Registry-driven property validation, aliases, labels, directed types and serialization. |
| `config.py` | Required environment configuration and secret-safe representation. |
| `client.py` | Lazy reusable driver, explicit database sessions, managed read/write transactions and shutdown. |
| `schema.py` | Registry-derived constraints and selected lookup indexes. |
| `service.py` | Single/batch upserts, revision handling, transaction boundaries and reconciliation. |
| `validation.py` | Read-only validation, topology constraints and expected-payload reconciliation. |
| `cli.py` | Explicit CLI commands and an offline synthetic sample generator. |

The only change to the ontology service is a public `validate_property_value()` wrapper
around its existing value validator. Graph code uses it for both node and relationship
properties. No YAML definitions were changed or copied into graph code.

## Mapping and identities

Run `citi-kg describe` to obtain the complete current mapping: all 36 classes, inherited
properties, required flags, datatypes, enums, keys and all 32 directed relationships.
It reads the registry at runtime, so there is no second ontology inventory to maintain.

- Concrete instances have `CitiKGEntity`, their exact ontology class ID and all ancestor
  labels. Abstract classes cannot be directly instantiated. There are 33 concrete classes.
- Class IDs must be canonical. Source property synonyms are mapped through the registry;
  two fields mapping to one property are rejected rather than silently choosing a value.
- Relationship synonyms resolve to canonical types, e.g. `HAS_CONTRACT` becomes `PARTY_TO`.
  Endpoints and direction are checked with `OntologyRegistry.validate_relation()`.
- `Contract` and `ContractDocument` remain different nodes. `EVIDENCED_BY` points from
  an allowed subject to a Document subtype. No `EVIDENCES` or business `Chunk` type is invented.
- Relationship business properties come from the relation definition. Currently only
  `POTENTIAL_ALTERNATIVE_TO` declares one: required `validation_status`.
- Strings, booleans, signed 64-bit integers, finite floats and homogeneous scalar lists
  are persisted as Neo4j properties. Dates/year-months stay validated ISO strings.
  Money remains integer cents. Optional null values are stored as property absence.

Canonical IDs are SHA-256 over unambiguous canonical JSON tuples:

| Kind | Identity components |
| --- | --- |
| Keyed entity/document/fact | discriminator, namespace, class ID, declared ontology key value |
| Clause | discriminator, namespace, contract identity, clause class, occurrence ID |
| Logical relationship | discriminator, namespace, canonical relationship type, source key, target key |

Namespace is required and is an upstream identity boundary, such as a tenant/dataset
namespace. Different namespaces never merge. Identity strings are case-sensitive and
must be nonblank without surrounding whitespace; the graph does not infer aliases or
normalize names. An optional explicit entity `identity` must equal the declared key value
in `properties`. Class `id_pattern` is checked in addition to the existing value rules.
`Buyer.legal_name` is the ontology's explicit natural-key exception: renames require an
upstream identity/migration decision, not fuzzy graph merging.

### Clause occurrence contract

`RenewalClause`, `TerminationClause` and `PaymentTermsClause` require both
`contract_identity` and `occurrence_id` in the input envelope. Neither is added to the
ontology property dictionary. A generic `identity` is rejected for clauses.

The future extraction layer **must generate and persist a stable occurrence ID**:

- Reuse it across updates/re-ingestion of the same logical clause.
- Use distinct IDs for multiple clauses of the same class within one contract.
- Never derive it from mutable clause text or only from contract + clause class.
- The same occurrence ID under another contract is a different identity.

The Contract must already exist or be included in the same transaction. A `HAS_CLAUSE`
edge must point from the clause's identity contract. Relationships are explicit inputs;
the service does not infer or automatically manufacture them. There are no unresolved
identity cases for the current concrete classes after adopting this caller contract.

## Configuration and installation

Python >=3.13, with 3.13 the repository's intended version. Install with the existing uv workflow:

```powershell
uv sync --locked
```

If uv is installed inside this same `.venv`, use `uv sync --locked --inexact` to retain
the tool package. A Python-module invocation is also supported:

```powershell
.venv/Scripts/python.exe -m citi_project.services.knowledge_graph.cli describe
```

Required environment variable names:

```text
NEO4J_URI
NEO4J_USERNAME
NEO4J_PASSWORD
NEO4J_DATABASE
```

Existing optional ontology location: `CITI_ONTOLOGY_DIR`, or global CLI `--ontology-dir`.
There is no existing dotenv loader, so no implicit `.env` loading has been introduced.
Set environment variables through your deployment secret manager or interactive prompts:

```powershell
$env:NEO4J_URI = Read-Host 'Neo4j URI'
$env:NEO4J_USERNAME = Read-Host 'Neo4j username'
$kgPassword = Read-Host 'Neo4j password' -AsSecureString
$env:NEO4J_PASSWORD = [System.Net.NetworkCredential]::new('', $kgPassword).Password
Remove-Variable kgPassword
$env:NEO4J_DATABASE = Read-Host 'Neo4j database'
```

Do not echo or commit the values. Configuration repr and connection errors suppress them.
The application logs counts, not payloads or evidence. Importing modules and constructing a
client do not connect. Database access happens only through explicit client operations.
Each client owns one driver and each operation opens/closes a session for the configured
database. Close the client after all concurrent operations have finished.

## Schema

Target Neo4j 5.7+ or a compatible later release with relationship property uniqueness.
The Python driver is constrained to major version 6 in `pyproject.toml`/`uv.lock`.
Schema installation is explicit and idempotent; ingestion verifies required uniqueness
constraints rather than silently installing them. On unsupported servers it fails safely.

```powershell
uv run citi-kg check
uv run citi-kg init-schema
```

For the current ontology, initialization issues 65 idempotent statements:

- `citi_kg_entity_key`: unique `CitiKGEntity._kg_key`.
- `citi_kg_scope_key`: unique `CitiKGScope.namespace`.
- `citi_kg_<relationship-id-lowercase>_key`: unique `_kg_key` on each of 32 relationship types.
- `citi_kg_scope_class`: index on `CitiKGEntity(_kg_namespace, _kg_class)`.
- `citi_kg_lookup_<class-id-lowercase>`: 30 indexes on concrete keyed classes using
  `(_kg_namespace, <ontology key>)`. This includes document identifiers. No index is
  generated for every business property; clauses use their canonical identity index.

Uniqueness indexes are created by their constraints, not duplicated. Constraint signatures
are checked, so an unrelated same-named constraint cannot satisfy the ingestion precondition.
Both Cypher 5 and Cypher 25 uniqueness type names are recognized, following
[Neo4j's SHOW CONSTRAINTS documentation](https://neo4j.com/docs/cypher-manual/current/schema/constraints/list-constraints/).
Schema is database-wide but uses feature-specific names; unrelated records are not deleted.
No schema or business graph was automatically created during implementation.

## Structured input and ingestion

Top-level JSON has `namespace`, the current `ontology_version`, `entities` and `relationships`.
The version must match the loaded registry. Obtain it using `describe`, or generate a full
working sample without credentials or network access:

```powershell
uv run citi-kg describe
uv run citi-kg sample --output kg-sample.json
uv run citi-kg ingest kg-sample.json --dry-run
```

The sample is intentionally synthetic and unreviewed. It contains four nodes and four
relationships, including the Contract, ContractDocument and RenewalClause example.
The dry run checks mapping only; it cannot establish that references to existing database
nodes resolve. Do not treat it as graph-integrity validation.

Example entity entries (inside a complete payload):

```json
{
  "class_id": "Vendor",
  "revision": 1,
  "properties": {
    "vendor_id": "V-001",
    "legal_name": "Example Supplier",
    "vendor_type": "Technical",
    "status": "Active"
  }
}
```

```json
{
  "class_id": "RenewalClause",
  "contract_identity": "CTR-001",
  "occurrence_id": "renewal-section-1",
  "revision": 1,
  "properties": {"automatic_renewal": true, "notice_days": 60},
  "provenance": [{
    "document": {"class_id": "ContractDocument", "identity": "V-001_contract_sow"},
    "property_id": "notice_days",
    "evidence_ref": "synthetic-example/renewal-1",
    "review_state": "unreviewed"
  }]
}
```

Example relationship entry:

```json
{
  "type": "PARTY_TO",
  "source": {"class_id": "Vendor", "identity": "V-001"},
  "target": {"class_id": "Contract", "identity": "CTR-001"},
  "properties": {},
  "revision": 1
}
```

Explicit database ingestion and reconciliation:

```powershell
uv run citi-kg ingest kg-sample.json
uv run citi-kg ingest kg-sample.json
uv run citi-kg validate --namespace kg-example --expected kg-sample.json --exact
```

Python callers can use `KnowledgeGraphService.ingest(GraphPayload(...))`,
`upsert_entity`, `upsert_entities`, `upsert_relationship` or `upsert_relationships`.
Batch helpers take `(namespace, instances)` and use the loaded registry version. Create a
`Neo4jClient(Neo4jConfig.from_env())` in a context manager and inject it alongside the registry.

### Updates, transaction safety and limits

Each input is a **complete property replacement**, not a patch. Required fields must be
present. Omitted/null optional properties and omitted provenance are removed on an update.
`revision` defaults to 1; changing any record requires a strictly greater integer revision.
An identical record with the same revision is a no-op. Older revisions or changed values
with the same revision fail and roll back the batch. Conflicting duplicate identities in
one payload are rejected; identical duplicates collapse deterministically.

Relationships have one logical edge per directed endpoint/type tuple. Multiple citations
are combined into that edge's provenance input, not duplicate edges. Reversing endpoints
or changing an identity/type means a different logical record, not an in-place update.
Automatic deletion/revocation/endpoint replacement is deliberately not exposed; a future
authorized migration/removal API must define those semantics before enabling removal.

One namespace is pinned to one ontology version. Use a new namespace for an explicitly
managed new-version load; no silent schema migration occurs. `CitiKGScope` is a technical
lock/version record, not a business entity. Writers serialize on it, validate the resulting
topology, use bounded UNWIND batches within one transaction, then compare the read-back
with the intended full namespace. A failed write/reconciliation rolls back all data changes.
Driver managed transactions retry transient failures; callbacks do not change external state.

The initial implementation validates the whole namespace, capped at 10,000 entities and
10,000 relationships; exceeding either limit fails, not silently truncates. Python callers
can explicitly change `max_records` and `batch_size` (default 500). Large deployments need
partitioning or a measured incremental-validation design before increasing these bounds.
Concurrent validation detects managed-writer revision changes and requests a retry.
External writers bypassing this service are outside the locking/revision protocol.

## Provenance and evidence

Use the existing ontology Document subtypes and `EVIDENCED_BY` business relationships.
The optional typed `Evidence` envelope is separate technical metadata, serialized as a
canonical JSON array in `_kg_provenance`, with these optional fields:

`document`, `property_id`, `source_ref`, `section`, `page`, `chunk_id`, `extraction_run`,
`evidence_ref`, `confidence`, `review_state`, `verification_state`.

At least one document/source/evidence reference is needed for each citation. Document
references must resolve to a Document subtype in the same namespace. Page/section/chunk
locations require that document reference. Pages are positive integers or null; confidence
is finite and within 0-1. `property_id`, when present, must be an actual canonical property
of the cited entity/relationship. Duplicate/order-varied citations serialize identically.

These fields do not become ontology business properties. In particular, source page/quote
semantics are not added to the declared `EVIDENCED_BY` property schema. Raw quote text and
chunks remain upstream; `evidence_ref` points to retained evidence. This module checks
references and metadata shape, not quote truth, chunk ownership, page existence, entitlement
or upstream approval authority. Those checks belong to the future extraction/review layer.
`review_state` is explicit metadata (`unreviewed`, `proposed`, `approved`, `rejected`), not an
approval mechanism. This infrastructure can store any of those states; production callers
must apply their approval policy before ingestion/query use. Missing provenance stays missing.

## Validation and tests

```powershell
uv run pytest -q
uv run citi-kg validate --namespace kg-example
uv run citi-kg validate --namespace kg-example --expected kg-sample.json
```

`ValidationReport` returns counts by class/type and issues for duplicate identities,
invalid/abstract classes, required properties, value/ID constraints, metadata/version/label
inconsistency, unknown or wrongly directed relationships, unresolved managed endpoints,
cross-namespace links, missing contract/document references, cardinality and disjointness.
Neo4j itself prevents physically dangling edges; the validator additionally checks that
endpoints are valid managed ontology instances. Business constraints stated only in prose,
such as cross-field risk rules, are not inferred as executable ontology rules.

Expected-payload reconciliation compares identities, properties, revisions, endpoints and
provenance, not only counts. By default it checks the expected subset. `--exact` additionally
requires namespace counts to equal the deduplicated payload. Validation uses only read
queries and never repairs data. CLI exit codes are 0 for success, 1 for validation findings,
and 2 for input/configuration/connection errors.

Normal tests require no live service. The transactional driver double verifies behavior,
parameterization and rollback but is not a Cypher engine. Live compatibility remains an
explicit integration gate:

```powershell
$env:NEO4J_TEST_DATABASE = Read-Host 'Dedicated database name beginning kg-test-'
uv run pytest tests/integration/test_neo4j_graph.py --neo4j-integration -q
```

The designated test database must already exist and be disposable/dedicated. Tests use
`NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` and `NEO4J_TEST_DATABASE`; they never fall
back to the application database. Both the switch and a test database with the required
prefix are mandatory. Each test creates a random namespace, checks connection/schema,
ingestion/replay/update or concurrent replay, and cleans only its own namespace. Shared
schema objects are left in place. No production/shared database is tested automatically.

## Future pipeline responsibilities

PDF/document retention -> extraction/chunking -> ontology instance creation -> this layer.
The upstream layer must resolve canonical business IDs, persist stable clause occurrence
IDs and record revisions, preserve source document/chunk/evidence records, enforce approval
and entitlement policy, and then submit `GraphPayload` with the matching ontology version.
It must not index both renditions of a document or use golden answers as business evidence.
No PDF parser, embedding store, extraction prompts, approval workflow or UI is included here.

The transaction design follows the official [Neo4j Python managed transaction documentation](https://neo4j.com/docs/python-manual/current/transactions/).


## HTTPS Aura Query API transport

`NEO4J_TRANSPORT` selects `bolt` (default, existing Python driver) or `http`.
Both implement the callback contract in `transport.py`; use `create_client(config)`
when constructing the service. Mapping, identity, evidence, schema and validation
are shared unchanged. No connection is made at import or client construction.

For HTTPS set `NEO4J_QUERY_API_URL` to an HTTPS **base URL** without a path,
credentials, query or fragment. The client appends `/db/<encoded-database>/query/v2`.
If omitted, it derives the base URL only from an Aura `.databases.neo4j.io`
hostname in `NEO4J_URI`, dropping the Bolt port. An explicit HTTPS base URL does
not require `NEO4J_URI`. Username, password and database remain required.

Connectivity-only PowerShell (from repository root, existing virtual environment):

```powershell
$env:NEO4J_TRANSPORT = 'http'
$env:NEO4J_QUERY_API_URL = Read-Host 'Aura HTTPS base URL (https://<host>)'
$env:NEO4J_USERNAME = Read-Host 'Database username'
$env:NEO4J_DATABASE = Read-Host 'Database name'
$kgPassword = Read-Host 'Database password' -AsSecureString
$env:NEO4J_PASSWORD = [System.Net.NetworkCredential]::new('', $kgPassword).Password
Remove-Variable kgPassword
.\.venv\Scripts\citi-kg.exe check
```

`check` sends only `RETURN 1 AS ok` with read routing, in one HTTPS request.
It does not initialize schema or ingest data. The existing `init-schema`, `ingest`
and `validate` commands select the same transport from configuration.
Basic authentication is sent only to the configured HTTPS origin; redirects are
refused, and neither authentication headers nor server error bodies are logged.
TLS certificate and hostname verification remain enabled through Python's default
SSL context. HTTPS may still require the corporate CA to be trusted by Python;
it does not bypass certificate verification. No extra HTTP dependency is needed.

Each service callback runs inside a Query API explicit transaction: begin, execute
parameterized statements, commit; callback failures trigger best-effort rollback.
Aura's `neo4j-cluster-affinity` header is retained throughout the transaction.
Successful commit bookmarks are passed into subsequent transactions on that client.
HTTP 202 responses are checked for API errors. Responses are fully consumed and
plain JSON fields/values adapt to the existing result interface (maps, lists,
strings and numbers used by this layer; not a general Neo4j type decoder).
The client serializes callbacks, uses a 60-second request timeout and does not
retry automatically. An unconfirmed commit may have succeeded: reconcile with
`validate --expected` before rerunning the same idempotent payload. A rollback
failure leaves the uncommitted transaction for server-side expiry. Separate schema
statements remain separately committed, idempotent operations as with Bolt.

Run offline coverage, including HTTP lifecycle and service replay/rollback:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

HTTP tests use injected openers, never a live database. The existing opt-in live
integration profile currently exercises Bolt only; HTTPS live compatibility must
be verified explicitly in an approved isolated environment. No live checks are
performed automatically.

Protocol references: [Query API transactions](https://neo4j.com/docs/query-api/current/transactions/),
[authentication](https://neo4j.com/docs/query-api/current/authentication-authorization/),
[read routing](https://neo4j.com/docs/query-api/current/routing/).
