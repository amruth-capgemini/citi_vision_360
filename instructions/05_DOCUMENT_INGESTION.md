# Phase 5 — Upload, extract, chunk and classify documents

## Goal

Process a vendor contract into a durable document and page-aware chunks, then propose a valid ontology document class. Keep every stage resumable and independently observable.

## Entry

Phase 4 produces stable vendor entities. Confirm existing file storage, PDF/DOCX extractors, job runner, vector service, file limits and auth in Phase 0 findings.

## Deliverables

`api/routes/semantic_documents.py`, `services/ontology/document_pipeline.py`, `prompts/semantic/classify_document.txt`, an extraction adapter exposing `extract_pages(...)`, and document pipeline tests. Add a `SemanticVectorService` adapter around the existing engine rather than binding business logic to pgvector/Neo4j vectors. If a background worker exists, use it; otherwise use a safe bounded task approach and persist status before processing.

## Workflow

`POST /semantic/documents` accepts file, optional source ID and class hint. Enforce size/type/auth and safe filename/path handling; preserve original bytes under the existing storage abstraction. Hash SHA256 and deduplicate within the correct tenant/source scope. Record `uploaded`, stage transitions and retryable `failed_stage/error`; `POST /semantic/documents/{id}/retry` resumes idempotently.

Extract a list of `{page: integer|null, text}` while preserving genuine PDF page numbers. DOCX may have `page=null`; never invent pagination. Chunk around 1,000–1,500 characters with 100–200 overlap, preferring headings, paragraphs, numbered sections and sentence boundaries. Store sequence, page range and character offsets with each exact chunk text. Define offsets relative to normalized extracted text and test reconstruction/overlap semantics. Embed chunks through the vector service, with batch/retry behavior and explicit dimensional compatibility.

Classify from `registry.document_classes()` only. Pass valid IDs to the LLM, validate constrained output and keep classification `proposed` with confidence/rationale; a caller's class hint is a hint, not authority. No relation/entity/contract fact extraction in this phase.

## Tests and exit gate

Ingest a short PDF with known pages and a DOCX; verify hash dedup, original bytes, chunk text/offsets, real versus null pages, candidate class validation, status transitions and isolated retry. Confirm an unsupported model class cannot be persisted. Show one contract classified as `Contract` or valid subtype. Stop and report before Phase 6.
