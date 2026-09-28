# Phase 6 — Clause segmentation and evidence-checked facts

## Goal

Extract focused, ontology-constrained contract terms with verifiable evidence. A proposed value without supporting source text must not become approved truth.

## Entry

Phase 5 contract and chunks are persisted with reliable provenance. Verify the registry schema and structured-output facilities.

## Deliverables

`prompts/semantic/{classify_clause,extract_facts}.txt`, `services/ontology/validators.py`, normalization helpers and tests. Extend `document_pipeline.py` in small steps; store `semantic_clauses` and `semantic_facts` with versions/status/evidence. Expose review list and approve/reject/edit for facts and clauses with audit events; add a UI view showing quote and surrounding chunk if a review UI already exists.

## Workflow

Classify chunks/headings only against Clause subclasses (`RenewalClause`, `TerminationClause`, `PaymentTermsClause` in MVP), merging contiguous segments where justified. A clause keeps ordered chunk IDs and source page span. Extract document-level dates/party/contract ID separately from per-clause properties. Generate a per-class JSON Schema from the registry; ask focused LLM calls for only allowed property IDs. Require `{value, confidence, evidence:{chunk_id,page,quote}}` for present values; represent absent values as null without guessed evidence.

Validate class/property compatibility, datatype, enum, chunk ownership, quoted text existence and page consistency. Normalize whitespace for exact quote matching; a bounded fuzzy check may flag a proposed quote for review, never silently certify it. Reject fabricated chunks/pages and unsupported IDs. Preserve both `value_raw` and normalized date/duration/money (including currency); record extractor model, prompt and ontology version. Distinguish `30 calendar days` from `30 business days`, and preserve jurisdiction/date ambiguity rather than guessing.

Review must show property, raw and normalized value, confidence, document/page, quote and nearby text. Editing keeps original and new values plus reviewer/time in audit history. Automatic approval remains disabled. Facts whose evidence fails remain rejected or clearly flagged and excluded from projection/retrieval as authoritative facts.

## Tests and exit gate

Use a known fixture containing auto-renewal, 30-day notice, termination and payment terms. Verify extracted values against literal contract spans, absent values remain null, a fake quote/page is rejected, and edits/approvals are audited. Manually inspect one extraction report. Stop before Phase 7.
