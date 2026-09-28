# Phase 9 — Governed retrieval and agent tools

## Goal

Let agents query reviewed semantic data with evidence, while preserving the existing Postgres/Neo4j/vector retrieval path and metadata tools.

## Entry

Phase 8 projection and manual Cypher checks pass. Inspect current `combined_retrieval`, KG agent tools, schema resolver, authorization and reranker.

## Deliverables

`services/ontology/retrieval.py` (or established equivalent) and small extensions to `agents/kg_agent/kg_tools.py`, agent tool registration and `combined_retrieval`. Add tests with an approved contract fixture.

## Service contract

Implement `search_ontology`, `search_entities`, `search_chunks`, `search_clauses`, `search_tables` and `search_columns`, returning standardized `{kind,id,score,source,ontology_version,evidence_ref}` candidates. Combine keyword, graph, vector and ontology lookup through the existing reranking path; scope by tenant/source/status. Do not expose proposed or rejected facts as verified answers. Vector search identifies candidates; Postgres review/evidence validation determines their authority.

Expose guarded tools: `ontology_lookup`, `find_entities`, `get_entity_profile`, `search_clauses`, `get_related_entities`, `tables_for_class`, `columns_for_property`, `get_entity_evidence`, `compare_contract_to_data` (the final tool can be a validated interface stub until Phase 10). Translate synonyms such as supplier → Vendor through registry. Validate all filter properties, class/relation IDs, traversal depths and result limits. Use parameterized graph/SQL queries and existing tool execution controls.

`get_entity_profile(Vendor)` returns canonical name, aliases, source rows, approved contracts/clauses/relations, review status and citations to exact document chunks. `search_clauses("How can we cancel AWS?")` should consider TerminationClause and RenewalClause and return contract ID, page when real, quote and relevant approved facts. `tables_for_class(Vendor)` returns approved physical mappings for later SQL planning.

## Tests and exit gate

Ask “Which vendors auto-renew with less than 60 days' notice?” The answer identifies only approved vendors/clauses, includes a contract quote and genuine page when available, and gives no invented citation. Test synonym lookup, property filter rejection, source isolation, proposed-record exclusion and unchanged baseline retrieval. Stop and report before Phase 10.
