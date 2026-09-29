# Citi Vision 360

The YAML ontology is the source of truth for business classes, properties and directed
relationships. The Neo4j layer accepts typed ontology instances independently of document
extraction.

See [the Knowledge Graph runbook](instructions/NEO4J_KNOWLEDGE_GRAPH.md) for architecture,
identity (including caller-supplied clause occurrence IDs), configuration, JSON inputs,
schema initialization, ingestion, validation and optional isolated integration tests.

```powershell
uv sync --locked
uv run pytest -q
uv run citi-kg describe
uv run citi-kg sample --output kg-sample.json
uv run citi-kg ingest kg-sample.json --dry-run
```

The commands above do not connect to Neo4j. Database operations are explicit and require
environment configuration. No PDF extraction pipeline is implemented by this feature.
