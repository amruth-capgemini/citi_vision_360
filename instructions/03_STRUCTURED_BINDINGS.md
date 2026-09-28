# Phase 3 — Bind structured tables and columns

## Goal

Propose physical-to-business mappings for a vendor table, validate them against ontology and require human approval before materialization.

## Entry

Phase 2 migration and sync pass. Inspect existing source/table/column metadata, comprehensive analysis, LLM client, reviewer auth and API conventions.

## Deliverables

`services/ontology/table_binder.py`, `prompts/semantic/bind_table.txt`, focused binding validation tests, and review endpoints for bindings in `api/routes/semantic_review.py`. Add a minimal review UI only if it fits the current frontend pattern; otherwise return all evidence needed for review by API and defer the broader UI to a separately recorded task.

## Workflow

`bind_source(db, source_id)` reads table name/description, domain, subdomain, column names/types/descriptions/synonyms/business meaning, PK/FK metadata, sample values and comprehensive analysis. Scope all reads to the source/tenant. Build a useful table summary. Shortlist approximately eight class candidates using existing vector search if available; provide a deterministic lexical fallback for a cold or offline index. Similarity provides candidates, never truth.

Pass only those candidate class IDs and allowed inherited properties to a structured-output LLM call. Validate its response IDs, table/property compatibility, datatype hints, duplicate column mappings, confidence range and source identity. Treat `_id`, date/time, amount/currency and email rules as hints; never let suffixes silently determine meaning. Persist table and column bindings with `status=proposed`, rationale, method and ontology version. Avoid writing a mapping for a missing/unsupported column.

Review API should list pending mappings with relevant table/column context and permit approve/reject/edit. Validate an edited target against the current ontology. Record reviewer, timestamp and immutable before/after event. Define a complete approved binding set for a table (including identity key and canonical name) before Phase 4 can materialize its rows. Authorization follows existing project rules.

## Tests and exit gate

With `vendors.csv`, propose `vendor_master → Vendor`, `vendor_id → Vendor.vendor_id`, `vendor_name → Vendor.name`, and optional `risk_rating → Vendor.risk_rating` if defined. Reject hallucinated class/property IDs, inappropriate types and cross-source approvals. Test approval, rejection and audit history; ensure nothing becomes an entity before approval. Run repository tests and report results. Stop before Phase 4.
