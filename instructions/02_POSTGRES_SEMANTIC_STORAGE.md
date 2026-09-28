# Phase 2 — Postgres semantic storage and ontology sync

## Goal

Persist semantic truth, provenance and review independently of the existing metadata catalog. Make Neo4j a rebuildable projection of approved records in a later phase.

## Entry

Phase 1 tests pass. Review real SQLAlchemy/Alembic patterns, key types, tenancy and migration history before designing tables.

## Deliverables

Create `core/models_semantic.py`, an Alembic migration, `services/ontology/sync.py`, and focused model/migration/sync tests. Register the models and migration in the actual project conventions.

## Tables

| Table | Essential fields |
| --- | --- |
| `ontology_versions` | version PK, normalized content, loaded_at. |
| `ontology_classes` | id, version, label, definition, parent_id, module, fibo_uri. |
| `ontology_properties` | class_id, property_id, datatype, required, cardinality, description, enum_values, ontology_version. |
| `ontology_relations` | relation_id, from_class, to_class, cardinality, ontology_version. |
| `semantic_bindings` | source_id, object_path, optional column_name, class_id, optional property_id, method, confidence, rationale, status, ontology_version, reviewer/timestamps. |
| `semantic_entities` | UUID, class_id, canonical_name, attributes JSONB, status, created_from, embedding_text, timestamps, ontology_version. |
| `semantic_entity_aliases` | entity_id, type, raw/normalized value, source_id, object_path, column_name; stable ID/name/legal name/abbreviation. |
| `semantic_documents` | UUID, source_id, filename/type, storage_path, sha256, class/confidence, status, failed_stage/error, uploader, timestamps, ontology_version. |
| `semantic_document_chunks` | UUID, document_id, sequence, nullable page range, char range, text, optional embedding. |
| `semantic_clauses` | UUID, document_id, class_id, title, ordered chunk references, confidence, status, ontology_version. |
| `semantic_facts` | UUID, typed subject, class/property IDs, raw and normalized value, datatype, confidence, evidence chunk/quote/page, extractor/prompt/ontology versions, review status/reviewer. |
| `semantic_relations` | UUID, allowed relation ID, subject entity, exactly one supported object target (entity/document/clause), properties JSONB, evidence, method/confidence, ontology_version/status. |
| `semantic_review_events` | object kind/ID, action, before/after JSONB, actor, timestamp. |

Use explicit keys and constraints: version-scoped ontology IDs, unique document hash within appropriate tenant/source scope, unique chunk `(document_id, sequence)`, no ambiguous alias uniqueness across classes/tenants, valid status values, foreign keys and indexes for review queues, source lookup and evidence. Decide whether a source row key or lineage table is needed to make `RECORDED_IN` and SQL filtering reliable. Preserve the possibility of proposed replacements across ontology versions.

Do not require pgvector if the repository lacks it. If installed, use the established dimension/index conventions; otherwise keep embedding storage behind a service interface in Phase 5. Avoid JSON embedding fallback as an unexamined performance choice.

`sync_ontology_to_db(db)` loads the registry and hash, no-ops when the version exists, and transactionally inserts a new version and its class/property/relation snapshots. Never mutate old snapshots or approved facts. Invoke on startup/manual refresh only after confirming startup/migration order and failure handling; no graph projection here.

## Tests and exit gate

Run migration up/down on an isolated test DB, model constraint tests, sync twice for idempotence and changed ontology for a new version, and verify old snapshots survive. If DB services are unavailable, report exactly which integration gate remains and stop before Phase 3.
