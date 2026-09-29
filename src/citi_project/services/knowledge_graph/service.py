"""Atomic, namespace-scoped ingestion of validated ontology instances."""

from collections import defaultdict
from dataclasses import dataclass
import logging

from .transport import GraphClient
from .identity import text_id
from .mapping import GraphMapper, identifier
from .models import GraphInputError, GraphPayload
from .schema import ensure_schema, require_constraints
from .validation import GraphValidator, topology_report

log = logging.getLogger(__name__)

LOCK_SCOPE = """// citi-kg:lock-scope
MERGE (s:CitiKGScope {namespace: $namespace})
ON CREATE SET s.ontology_version = $version
SET s.write_lock = coalesce(s.write_lock, 0) + 1
RETURN s.ontology_version AS version"""


def node_query(labels):
    # All labels have already been allow-listed through the registry.
    extra = ":".join(identifier(label) for label in labels if label != "CitiKGEntity")
    return ("// citi-kg:upsert-nodes\nUNWIND $rows AS row\n"
            "MERGE (n:CitiKGEntity {_kg_key: row.key, _kg_namespace: $namespace})\n"
            f"SET n:{extra}\nSET n = row.properties\nRETURN count(n) AS count")


def relationship_query(type_id):
    return ("// citi-kg:upsert-edges\nUNWIND $rows AS row\n"
            "MATCH (s:CitiKGEntity {_kg_key: row.source, _kg_namespace: $namespace})\n"
            "MATCH (t:CitiKGEntity {_kg_key: row.target, _kg_namespace: $namespace})\n"
            f"MERGE (s)-[r:{identifier(type_id)} {{_kg_key: row.key}}]->(t)\n"
            "SET r = row.properties\nRETURN count(r) AS count")


@dataclass(frozen=True)
class IngestionResult:
    input_entities: int
    input_relationships: int
    unique_entities: int
    unique_relationships: int
    written_entities: int
    written_relationships: int
    total_entities: int
    total_relationships: int


class KnowledgeGraphService:
    def __init__(self, registry, client: GraphClient, *, batch_size=500, max_records=10000):
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.registry, self.client = registry, client
        self.mapper = GraphMapper(registry)
        self.validator = GraphValidator(self.mapper, max_records=max_records)
        self.batch_size = batch_size

    def ensure_schema(self):
        return ensure_schema(self.client, self.registry)

    def ingest(self, payload):
        mapped = self.mapper.map(payload)  # invalid input never opens a connection
        if len(mapped.nodes) > self.validator.max_records or len(mapped.edges) > self.validator.max_records:
            raise GraphInputError("Payload exceeds configured record limit")
        result = self.client.write(self._ingest, mapped)
        log.info("Graph ingestion complete: entities=%d relationships=%d", result.written_entities, result.written_relationships)
        return result

    def upsert_entity(self, namespace, entity):
        return self.upsert_entities(namespace, [entity])

    def upsert_entities(self, namespace, entities):
        return self.ingest(GraphPayload(namespace, self.registry.ontology_version, tuple(entities)))

    def upsert_relationship(self, namespace, relationship):
        return self.upsert_relationships(namespace, [relationship])

    def upsert_relationships(self, namespace, relationships):
        return self.ingest(GraphPayload(namespace, self.registry.ontology_version, relationships=tuple(relationships)))

    def _ingest(self, tx, mapped):
        require_constraints(tx, self.registry)
        scope = tx.run(LOCK_SCOPE, namespace=mapped.namespace, version=mapped.ontology_version).single()
        if scope is None or scope["version"] != mapped.ontology_version:
            raise GraphInputError("Namespace is pinned to a different ontology version; use a new namespace")
        existing, nodes, edges = self.validator.snapshot(tx, mapped.namespace)
        if not existing.valid:
            raise GraphInputError("Existing namespace fails validation; inspect it before ingesting updates")
        node_map, edge_map = {n.key: n for n in nodes}, {e.key: e for e in edges}
        new_nodes = self._merge(node_map, mapped.nodes)
        new_edges = self._merge(edge_map, mapped.edges)
        if len(node_map) > self.validator.max_records or len(edge_map) > self.validator.max_records:
            raise GraphInputError("Resulting namespace exceeds configured record limit")
        proposed = topology_report(self.registry, list(node_map.values()), list(edge_map.values()))
        if not proposed.valid:
            raise GraphInputError("Batch violates endpoint, provenance, clause identity or relationship constraints")
        groups = defaultdict(list)
        for row in new_nodes:
            groups[row.labels].append({"key": row.key, "properties": row.properties})
        for labels, rows in sorted(groups.items()):
            self._write_batches(tx, node_query(labels), rows, mapped.namespace)
        groups = defaultdict(list)
        for row in new_edges:
            groups[row.type].append({"key": row.key, "source": row.source, "target": row.target, "properties": row.properties})
        for type_id, rows in sorted(groups.items()):
            self._write_batches(tx, relationship_query(type_id), rows, mapped.namespace)
        # Read back in the same transaction: failed reconciliation rolls back all writes.
        report, actual_nodes, actual_edges = self.validator.snapshot(tx, mapped.namespace)
        if (not report.valid or {n.key: n for n in actual_nodes} != node_map
                or {e.key: e for e in actual_edges} != edge_map):
            raise GraphInputError("Graph reconciliation failed; batch rolled back")
        return IngestionResult(mapped.input_entities, mapped.input_relationships, len(mapped.nodes), len(mapped.edges),
                               len(new_nodes), len(new_edges), report.node_count, report.relationship_count)

    @staticmethod
    def _merge(existing, incoming):
        changed = []
        for row in incoming:
            previous = existing.get(row.key)
            if previous == row:
                continue
            if previous is not None and row.properties["_kg_revision"] <= previous.properties["_kg_revision"]:
                raise GraphInputError("Changed records require a strictly newer revision")
            existing[row.key] = row
            changed.append(row)
        return changed

    def _write_batches(self, tx, query, rows, namespace):
        for start in range(0, len(rows), self.batch_size):
            batch = rows[start:start + self.batch_size]
            result = tx.run(query, rows=batch, namespace=namespace).single()
            if result is None or result["count"] != len(batch):
                raise GraphInputError("Write count mismatch; batch rolled back")

    def validate(self, namespace, expected=None, *, exact=False):
        text_id(namespace, "namespace")
        return self.client.read(lambda tx: self.validator.validate(tx, namespace, expected, exact=exact))
