# Phase 1 — Ontology YAML and registry

## Goal

Establish a small, governed ontology as code. This defines allowed meaning; it does not create vendor instances or graph edges from uploaded data. Build the first vertical slice for Vendor, Contract, Clause, RenewalClause, TerminationClause, PaymentTermsClause and Invoice; expand Technology/Risk later.

## Entry

Phase 0 assessment returned. Confirm actual Python package and test conventions. This phase must not require Postgres, Neo4j, vector services or live LLM calls.

## Deliverables

- `ontology/_schema.yaml` (or a JSON Schema referenced by it), `ontology/core.yaml`, `commercial.yaml`, `contracts.yaml` and `data.yaml`. Add `technology.yaml` and `risk.yaml` as small, valid extension stubs only if repository conventions need them; do not model an enterprise domain prematurely.
- `src/ingestion_platform/services/ontology/{__init__,models,registry}.py`, adjusted to actual package root.
- `tests/test_ontology_registry.py` with valid and invalid fixtures.

## Contract to implement

Each class has stable `id`, label, definition, module, optional parent, synonyms, optional explicit `fibo_uri`, and typed properties. Define property ID, datatype, required, cardinality, description and optional enum. Define relationship ID, allowed source/target class, cardinality and description. Include `PARTY_TO`, `HAS_CLAUSE`, `BILLED_UNDER` for the MVP. Choose and document the graph direction and whether Contract is a document class; keep it consistent in later phases.

Implement `OntologyRegistry` with `load()`, `get_class()`, `properties_for_class(include_inherited=True)`, `document_classes()`, `clause_classes()`, `validate_class()`, `validate_property()`, `validate_relation()`, `allowed_relations()`, and `json_schema_for_extraction(class_id)`. Resolve inheritance, reject duplicate IDs and missing parents, detect inheritance cycles, reject relation endpoints and unsupported datatypes, validate cardinalities and enum values. Return precise errors with module/file and offending ID.

Generate extraction JSON Schema from ontology properties only. A field's present value has `value`, `confidence`, and evidence `{chunk_id, page, quote}`; missing values can be null and must not require fabricated evidence. Disallow additional property IDs. Validate output again after LLM generation in later phases.

Compute `ontology_version` as a deterministic hash of normalized content sorted by stable ID, independent of YAML formatting and file order. Document how changing a definition changes the version. Keep `fibo_uri` optional; never load FIBO as the runtime class inventory by default.

## Tests and exit gate

Test inheritance and inherited properties, document/clause filters, allowed relation endpoints, duplicate IDs across modules, unknown parents, cycles, invalid types/cardinality/enums, unknown property IDs, additional JSON keys, and stable hash across formatting/order changes. Run the repository's test command for these tests. Report pass/fail and stop. **Do not begin Phase 2 until registry tests pass.**

## Handoff

Provide file list, example registry API usage, ontology hash, test command/results, unresolved naming decisions and the Phase 2 migration assumptions.
