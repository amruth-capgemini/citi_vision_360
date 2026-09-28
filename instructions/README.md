# Governed semantic layer: execution guide

Give this entire folder to Claude in the repository root. Start Claude with `00_REPOSITORY_ASSESSMENT.md`. The repository itself was not available when these files were written; paths below are hypotheses from the supplied code walkthrough and must be verified against the current checkout.

## Objective and first proof

Build one connected, governed flow: `vendors.csv` and `invoices.csv` plus vendor contracts → ontology bindings → reviewed entities, clauses, facts and relations → Neo4j projection → an agent answer that combines contract evidence and SQL spend. The first target question is: **Which vendors have auto-renewal clauses requiring less than 60 days' notice, and how much did we spend with them last quarter?**

Use these distinct layers throughout:

| Layer | Responsibility |
| --- | --- |
| YAML ontology and registry | Allowed classes, properties, relationships, constraints and synonyms. |
| Postgres | Authoritative bindings, canonical entities, raw and normalized facts, evidence, approval history and versions. |
| Neo4j | Rebuildable projection of approved ontology and semantic records, linked to existing metadata nodes. |
| Vector retrieval | Candidate discovery for classes, chunks and entities; similarity is never approval or merge evidence on its own. |
| Agents and SQL | Governed tools that retrieve evidence and query structured records through approved mappings. |

## Order of work

| File | Outcome | Gate |
| --- | --- | --- |
| `00_REPOSITORY_ASSESSMENT.md` | Verify the real code, dependencies and file plan. | Return assessment before significant code. |
| `01_ONTOLOGY_REGISTRY.md` | Small versioned YAML ontology and strict registry. | Registry tests pass. |
| `02_POSTGRES_SEMANTIC_STORAGE.md` | Semantic tables, migration and ontology sync. | Migration and sync tests pass. |
| `03_STRUCTURED_BINDINGS.md` | Propose and review vendor table/column mappings. | Vendor bindings approved in a test. |
| `04_CANONICAL_ENTITIES.md` | Materialize vendor entities and aliases. | Vendor rows yield stable canonical identities. |
| `05_DOCUMENT_INGESTION.md` | Upload, page-aware extraction, chunks and classification. | Contract ingests with provenance. |
| `06_CLAUSES_AND_FACTS.md` | Focused, evidence-checked extraction. | Known contract terms match source text. |
| `07_ENTITY_RESOLUTION.md` | Link contract parties to vendors and propose relations. | AWS contract resolves to existing AWS entity. |
| `08_NEO4J_PROJECTION.md` | Project approved records into existing graph. | Graph queries and rebuild pass. |
| `09_RETRIEVAL_AND_AGENT_TOOLS.md` | Governed semantic retrieval and contract answers. | Agent cites clause evidence. |
| `10_SQL_BRIDGE_AND_EVALUATION.md` | Cross-source question, evaluation and rollout checks. | Spend comes from SQL; contract terms from evidence. |

## Operating contract for Claude

1. Read `00_REPOSITORY_ASSESSMENT.md`, then the current phase file and relevant repository code. Report actual paths and differences from these instructions. Do not invent existing APIs or models.
2. Do only the current phase. Keep changes small. Reuse existing upload, extraction, LLM, database, vector and graph abstractions where suitable. Do not delete the existing Source/Table/Column/Domain/BusinessTerm/Metric/DataProduct graph or regress glossary and Text-to-SQL flows.
3. Treat model output as proposals. Validate IDs, datatypes, allowed endpoints and source evidence before persistence. Disable automatic semantic approval. Do not project unapproved instance data.
4. Add meaningful tests and run the current phase's gate. If a test or migration fails, stop, report the failure and fix it within the phase. Do not start the next file silently.
5. One phase corresponds to one reviewable commit when working in a Git repository. Inspect `git status` first; do not overwrite unrelated user changes or create a commit unless the user or existing repository workflow authorizes it.
6. End every phase with: **Files created; Files changed; Architecture implemented; Tests run; Tests passed/failed; Known issues; Next phase.** Include concrete commands and results.

## Cross-cutting rules

- Every binding, extraction and relation carries `ontology_version`. Every model-assisted operation records prompt/model version, evidence, validation and review status where applicable.
- Reprocessing after an ontology change creates proposed replacements; it does not overwrite approved truth.
- Stable enterprise IDs outrank name matches. Fuzzy or vector matches only shortlist candidates. Ambiguous merges require review.
- Preserve original document bytes, raw extracted values, chunk/page provenance and review audit events. DOCX page numbers remain null if unavailable.
- Use allow-listed ontology relation IDs when constructing Cypher; parameterize values. Never execute arbitrary model-generated Cypher or SQL.
- Keep FIBO alignment optional and explicit. The enterprise YAML is the operational ontology.
- A failed document has a visible stage/error and can be retried without halting the batch.

## Minimal fixtures

Create `vendors.csv` with `V001,Amazon Web Services,MEDIUM` and `V002,Acme Technologies,HIGH`; `invoices.csv` with vendor IDs, invoice/payment dates, amount and currency; and two small contracts with known renewal, termination and payment language. Pick fixed clock boundaries or inject a clock so “last quarter” is reproducible. Do not include invented page citations in fixture expectations.

**Starter instruction to Claude:** “Read `semantic-layer-plan/README.md` and `semantic-layer-plan/00_REPOSITORY_ASSESSMENT.md`. Inspect the repository and return the assessment first. Then implement Phase 1 only, run its tests and report the gate. Do not begin Phase 2.”
