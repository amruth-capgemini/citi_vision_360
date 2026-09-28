# Phase 0 — Repository assessment

## Goal

Before significant code, inspect the actual checkout and give a repository-specific implementation assessment. The attached walkthrough suggests FastAPI, SQLAlchemy/Postgres, Neo4j `GraphMetadataService`, a file upload connector, document extraction utilities, and `combined_retrieval`; all details must be verified in the current branch.

## Inspect and report

1. Existing modules worth reusing: upload and file exploration, comprehensive analysis, metadata models, document extraction, vector sync, graph service, ontology or FIBO artifacts, KG agent, Text-to-SQL resolver, frontend review conventions.
2. Relevant SQLAlchemy models and Alembic layout: catalog/source/object descriptions, approvals/audit, document records, migrations and async/sync session patterns. Map proposed semantic tables to current naming conventions without overloading existing catalog tables.
3. Vector setup: current embeddings model, storage engine, index/dimensions, batch/search APIs and whether `pgvector` is installed and migrated. State what abstraction Phase 5 should use.
4. Neo4j driver/session/service lifecycle, existing labels and relationship IDs, schema/index methods, rebuild semantics and collision risks. Confirm how `Source → Table → Column` nodes are keyed.
5. Upload bytes and storage lifecycle: `POST /upload/files`, source IDs, file dedup, object storage/local paths, extractors, supported PDF/DOCX types, file size limits and authorization.
6. LLM structured-output client, JSON Schema support, prompt storage and retry/timeout behavior. Distinguish native constrained decoding from post-response validation.
7. Background work/job mechanism, progress/status persistence, retries and failure isolation.
8. Existing approval/review API and UI patterns, reviewer identity and audit history.
9. Concrete conflicts with this plan, including whether the existing graph or the separate in-memory DocuGraph is in scope. Report migration, tenant/source isolation and security constraints.
10. Exact files to create and modify for **Phase 1**, with current package roots and test commands. Include what will be deferred.

## Suggested starting points to verify

`src/ingestion_platform/api/upload.py`, `api/routes/semantic_graph.py`, `api/routes/llm_analysis.py`, `connectors/file_upload_connector.py`, `services/metadata/graph_service.py`, `services/metadata/vector_sync_service.py`, `agents/kg_agent/kg_tools.py`, `agents/text_to_sql/schema_resolver_agent.py`, `utils/document_extractor.py`, `services/semantic_inventory.py`, `services/domain_overlay_loader.py`, `scripts/load_fibo_to_graph.py`, `frontend/lib/api.ts`.

These are leads from the supplied walkthrough, not confirmed code. Inspect call sites, migrations, config and tests as well.

## Required assessment format

Provide a short architecture map, an evidence table with actual file paths and symbols, conflicts/decisions, the Phase 1 change list and its test command. State what happens today after upload versus after explicit analysis/rebuild. In the supplied walkthrough upload stored bytes first; semantic work happened in later calls. Do not imply automatic ontology extraction already exists.

## Exit gate

Return the assessment **before** substantial implementation. Then start Phase 1 only. If the repository is unavailable or a critical dependency cannot be inspected, stop and request that checkout; do not manufacture repository-specific code.
