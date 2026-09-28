# Phase 8 — Neo4j projection of approved semantics

## Goal

Extend the current Neo4j metadata graph with approved ontology, bindings, entities, documents, clauses and relations. Postgres remains authoritative, and a projection can be cleared/rebuilt without losing review history.

## Entry

Phase 7 approved fixture records exist. Inspect actual graph keys and service lifecycle before writing Cypher; preserve Source/Table/Column/Domain/BusinessTerm/Metric/DataProduct nodes.

## Deliverables

`services/ontology/graph_projection.py` with `OntologyGraphProjector`: `ensure_schema`, `project_ontology`, `project_bindings`, `project_entities`, `project_documents`, `project_relations`, `clear_projection`, `rebuild_projection`. Tests using the established Neo4j fixture/driver or a separate integration profile. Never put all logic into `graph_service.py`.

## Graph shape

- `OntologyClass` and `OntologyProperty` nodes with version-aware identities; `SUBCLASS_OF` and `HAS_PROPERTY`, plus `ALIGNED_WITH` only for explicit FIBO URIs.
- Approved `Table -[:REPRESENTS]-> OntologyClass` and `Column -[:MAPS_TO_PROPERTY]-> OntologyProperty`, using existing physical node keys.
- `Entity -[:INSTANCE_OF]-> OntologyClass` and `Entity -[:RECORDED_IN {key_column,key_value}]-> Table` for verified row lineage.
- `Document -[:INSTANCE_OF]-> Contract`; `Contract -[:HAS_CLAUSE]-> Clause -[:EVIDENCED_BY]-> Chunk`; approved Vendor `PARTY_TO` Contract with role/evidence references.
- Simple approved scalar terms can be node properties when appropriate; full fact/evidence records remain in Postgres. Concepts needing independent relationships can become nodes.

Validate relationship ID against registry before constructing any dynamic relationship type; use only allow-listed identifiers and parameterized values. Project only approved instance records for the matching ontology version. Define how ontology definitions themselves appear independently of approval and how stale versions are retired. Stable UUID/version keys, constraints and indexes ensure idempotent `MERGE` without joining different tenants/sources. Full-text indexes for names/aliases are useful; avoid duplicate vector storage unless justified.

## Tests and exit gate

Run projection twice and compare counts/edges; rebuild from Postgres and compare again. Confirm existing metadata nodes/edges remain. Manually inspect `OntologyClass`, `Entity-[:INSTANCE_OF]`, `Vendor-[:PARTY_TO]->Document-[:HAS_CLAUSE]->Clause`, `Table-[:REPRESENTS]->OntologyClass` and `Entity-[:RECORDED_IN]->Table`. Validate that pending/rejected facts and relations are absent. Show actual Cypher and results; stop before Phase 9.
