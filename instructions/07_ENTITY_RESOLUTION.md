# Phase 7 — Contract party resolution and semantic relations

## Goal

Resolve parties mentioned in contracts to existing vendor entities and propose only ontology-allowed, evidence-backed relationships.

## Entry

Phase 6 has reviewed contract facts and Phase 4 has canonical vendor entities. Confirm party representation and relation direction from Phase 1.

## Deliverables

Extend `services/ontology/entity_resolver.py`; add `prompts/semantic/resolve_entity.txt` only for ambiguous tie-breaks; add relation creation/validation and entity/relation review endpoints plus tests. Extend a review screen when present to show candidate evidence, merge/split and relation approval.

## Workflow

Resolve in order: stable enterprise IDs in a known namespace → exact normalized alias → exact canonical name → fuzzy lexical candidates → embedding candidates → constrained LLM tie-break → proposed new entity. Similarity and LLM votes cannot auto-merge. For `Amazon Web Services, Inc.` and existing V001 `Amazon Web Services`, use documented alias/legal-name evidence and review where identity remains uncertain. Track why each candidate won and the source chunk that mentions the party.

Propose `Vendor -[:PARTY_TO {role:'supplier'}]-> Contract`, `Contract -[:HAS_CLAUSE]-> RenewalClause` and any relevant `BILLED_UNDER` relation only if endpoints/type match ontology. Validate endpoint identity, role/property schema, source evidence and ontology version before saving. A contract can have several parties; distinguish vendor from customer and avoid blanket party-to-vendor inference. Approval/rejection/merge/split emits audit events; prevent cyclic or inconsistent alias/merge state. Do not send proposals to Neo4j yet.

## Tests and exit gate

The AWS contract resolves to the existing V001 vendor UUID, with no duplicate vendor. Similar but distinct names remain separate pending review. Invalid relation ID or endpoint fails, unsupported/uncited relations do not pass, and all review transitions are audited. Stop before Phase 8.
