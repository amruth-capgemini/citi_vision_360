# Phase 10 — Text-to-SQL bridge and end-to-end evaluation

## Goal

Answer a cross-source business question with contract-backed terms and SQL-backed spend, then measure each semantic stage separately.

## Entry

Phase 9 answers a contract-only question with valid evidence. Invoice fixture and approved Vendor/Invoice mappings are available; verify join metadata and SQL safety in the current resolver.

## Deliverables

Small changes to `agents/text_to_sql/schema_resolver_agent.py` or the actual schema resolver, guarded `compare_contract_to_data`, `scripts/eval_semantic_extraction.py`, deterministic fixtures under `tests/fixtures/semantic/`, and end-to-end tests. Add a short runbook for migration, ontology sync, review, projection, rebuild and retry.

## SQL bridge

Resolve `AWS` → canonical Vendor entity → approved `RECORDED_IN` row key `V001` → approved Vendor and Invoice table/column mappings → validated invoice join. Infer the relevant date field (invoice date versus payment date) from mapped semantics and the question; ask for clarification or state a choice if ambiguous. Parameterize `vendor_id`, dates and filters. Query authoritative invoice values through the existing read-only SQL guard. Sum by currency unless a governed exchange-rate source and conversion policy exist; do not silently combine currencies. Define “last quarter” using the user's/business timezone and a test clock, with explicit inclusive/exclusive bounds.

`compare_contract_to_data(entity_id, contract_property, data_metric)` accepts ontology-approved inputs and returns both the contract fact with document/chunk evidence and a structured SQL query result with source/table, filters, dates and aggregation. Prevent unrestricted generated SQL/Cypher. Keep the contract conclusion distinct from the actual invoice calculation.

## First end-to-end acceptance

Question: **Which vendors have auto-renewal clauses requiring less than 60 days' notice, and how much did we spend with them last quarter?**

Expected path: `Vendor` and `RenewalClause.auto_renew/notice_days` through ontology → approved Vendor/Contract/Clause relations and evidence → canonical vendor IDs → approved physical bindings → invoice SQL for last-quarter totals → answer with contract quote/page and SQL amount/currency/time window. Test empty results, ambiguous quarter/date fields, mixed currencies and an unapproved clause.

## Evaluation and follow-on scope

Gold fixtures should annotate document class, clause boundaries/types, property values and spans, entity identity and relations. Report precision/recall/F1 separately for classification, clauses, property extraction, entity resolution and relations; add evidence support and answer correctness checks. Do not publish a single blended semantic score. Record latency, model/prompt/ontology versions, token use, candidate sets, validation outcomes and review decisions by stage. Calibrate any future auto-approval threshold from measured results; keep auto-approval disabled now.

Only after the first scenario works, plan an Application → Technology → Vendor → Contract traversal scenario; then risk/control/RCA. Revisit FIBO alignment, broader ontology modules, performance and deployment as separate reviewable work.

## Exit gate

Run end-to-end integration and evaluation commands; provide expected versus observed answer, exact evidence, SQL query/bounds and metrics. Confirm graph rebuild and document retry still work. Report failures and stop rather than expanding the ontology.
