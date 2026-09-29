"""Read-only graph inspection and reconciliation against the loaded ontology."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import json

from .mapping import GraphMapper
from .models import EntityInstance, EntityRef, Evidence, GraphInputError, RelationshipInstance


NODES = """// citi-kg:read-nodes
MATCH (n:CitiKGEntity {_kg_namespace: $namespace})
RETURN properties(n) AS properties, labels(n) AS labels
LIMIT $limit"""
SCOPE = """// citi-kg:read-scope
MATCH (s:CitiKGScope {namespace: $namespace})
RETURN s.ontology_version AS version, s.write_lock AS revision"""
EDGES = """// citi-kg:read-edges
MATCH (s)-[r]->(t)
WHERE r._kg_namespace = $namespace
   OR (s:CitiKGEntity AND s._kg_namespace = $namespace)
   OR (t:CitiKGEntity AND t._kg_namespace = $namespace)
RETURN properties(r) AS properties, type(r) AS type,
       s._kg_key AS source, t._kg_key AS target,
       s._kg_namespace AS source_namespace, t._kg_namespace AS target_namespace
LIMIT $limit"""


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str


@dataclass
class ValidationReport:
    node_count: int = 0
    relationship_count: int = 0
    nodes_by_class: dict[str, int] = field(default_factory=dict)
    relationships_by_type: dict[str, int] = field(default_factory=dict)
    expected_nodes: int | None = None
    expected_relationships: int | None = None
    matched_nodes: int | None = None
    matched_relationships: int | None = None
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def valid(self):
        return not self.issues

    def add(self, code, message):
        self.issues.append(ValidationIssue(code, message))


def evidence_from_json(value):
    try:
        decoded = json.loads(value)
        if not isinstance(decoded, list):
            raise ValueError
        result = []
        for item in decoded:
            # Canonical encoding is a sorted JSON array of citation objects.
            if not isinstance(item, dict):
                raise ValueError
            data = dict(item)
            if data.get("document") is not None:
                data["document"] = EntityRef(**data["document"])
            result.append(Evidence(**data))
        return tuple(result)
    except (ValueError, TypeError, KeyError):
        raise GraphInputError("Malformed stored provenance metadata") from None


def node_ref(row):
    p = row.properties
    return EntityRef(row.class_id, p.get("_kg_identity"), p.get("_kg_contract_identity"), p.get("_kg_occurrence_id"))


def topology_report(registry, nodes, edges):
    report = ValidationReport(len(nodes), len(edges), dict(Counter(n.class_id for n in nodes)),
                              dict(Counter(e.type for e in edges)))
    node_map = {n.key: n for n in nodes}
    if len(node_map) != len(nodes):
        report.add("duplicate_node", "Duplicate canonical node IDs")
    if len({e.key for e in edges}) != len(edges):
        report.add("duplicate_relationship", "Duplicate canonical relationship IDs")
    for row in (*nodes, *edges):
        if any(ref not in node_map for ref in row.references):
            report.add("missing_reference", "Unresolved contract or provenance document reference")
    outbound, inbound, pairs = defaultdict(set), defaultdict(set), defaultdict(set)
    for edge in edges:
        source, target = node_map.get(edge.source), node_map.get(edge.target)
        if source is None or target is None:
            report.add("missing_endpoint", "Relationship endpoint is absent or outside namespace")
            continue
        try:
            relation = registry.validate_relation(edge.type, source.class_id, target.class_id)
        except ValueError:
            report.add("invalid_relationship", "Unknown relationship or invalid directed endpoint classes")
            continue
        if edge.type == "HAS_CLAUSE":
            if target.properties.get("_kg_contract_identity") != source.properties.get("_kg_identity"):
                report.add("clause_contract", "HAS_CLAUSE disagrees with clause identity contract")
        outbound[(edge.type, edge.source)].add(edge.target)
        inbound[(edge.type, edge.target)].add(edge.source)
        pairs[(edge.source, edge.target)].add(edge.type)
        if relation.cardinality in ("one_to_one", "many_to_one") and len(outbound[(edge.type, edge.source)]) > 1:
            report.add("cardinality", "Relationship exceeds ontology maximum outgoing cardinality")
        if relation.cardinality in ("one_to_one", "one_to_many") and len(inbound[(edge.type, edge.target)]) > 1:
            report.add("cardinality", "Relationship exceeds ontology maximum incoming cardinality")
    for types in pairs.values():
        if any(set(registry.get_relation(t).disjoint_with) & types for t in types):
            report.add("disjoint_relationships", "Disjoint relationships connect the same directed endpoints")
    return report


class GraphValidator:
    def __init__(self, mapper: GraphMapper, *, max_records=10000):
        if type(max_records) is not int or max_records < 1:
            raise ValueError("max_records must be positive")
        self.mapper = mapper
        self.max_records = max_records

    def snapshot(self, tx, namespace):
        before = list(tx.run(SCOPE, namespace=namespace))
        raw_nodes = list(tx.run(NODES, namespace=namespace, limit=self.max_records + 1))
        raw_edges = list(tx.run(EDGES, namespace=namespace, limit=self.max_records + 1))
        after = list(tx.run(SCOPE, namespace=namespace))
        if len(raw_nodes) > self.max_records or len(raw_edges) > self.max_records:
            raise GraphInputError("Namespace exceeds validation record limit; partition the namespace or increase the explicit limit")
        nodes, edges, issues = [], [], []
        for raw in raw_nodes:
            try:
                p = raw["properties"]
                if p.get("_kg_ontology_version") != self.mapper.registry.ontology_version:
                    raise GraphInputError("Stored ontology version differs from loaded registry")
                entity = EntityInstance(p.get("_kg_class"), {k: v for k, v in p.items() if not k.startswith("_kg_")},
                                        p.get("_kg_identity"), p.get("_kg_contract_identity"), p.get("_kg_occurrence_id"),
                                        p.get("_kg_revision"), evidence_from_json(p.get("_kg_provenance")))
                node = self.mapper.node(namespace, entity)
                if node.properties != p or set(node.labels) != set(raw["labels"]):
                    raise GraphInputError("Stored node identity, properties or labels differ from canonical mapping")
                nodes.append(node)
            except GraphInputError as exc:
                issues.append(ValidationIssue("invalid_node", str(exc)))
            except (TypeError, KeyError):
                issues.append(ValidationIssue("invalid_node", "Malformed stored node metadata"))
        by_key = {n.key: n for n in nodes}
        for raw in raw_edges:
            try:
                p = raw["properties"]
                source, target = by_key.get(raw["source"]), by_key.get(raw["target"])
                if source is None or target is None:
                    issues.append(ValidationIssue("missing_endpoint", "Endpoint absent, invalid or outside namespace"))
                    continue
                if raw["source_namespace"] != namespace or raw["target_namespace"] != namespace:
                    raise GraphInputError("Cross-namespace endpoint")
                rel = RelationshipInstance(raw["type"], node_ref(source), node_ref(target),
                                           {k: v for k, v in p.items() if not k.startswith("_kg_")},
                                           p.get("_kg_revision"), evidence_from_json(p.get("_kg_provenance")))
                edge = self.mapper.edge(namespace, rel)
                if edge.properties != p or edge.type != raw["type"]:
                    raise GraphInputError("Noncanonical stored relationship")
                edges.append(edge)
            except GraphInputError as exc:
                issues.append(ValidationIssue("invalid_relationship", str(exc)))
            except (TypeError, KeyError):
                issues.append(ValidationIssue("invalid_relationship", "Malformed stored relationship metadata"))
        report = topology_report(self.mapper.registry, nodes, edges)
        report.issues.extend(issues)
        if before != after:
            report.add("concurrent_write", "Namespace changed during read-only validation; retry the check")
        if len(after) > 1 or (after and after[0]["version"] != self.mapper.registry.ontology_version):
            report.add("invalid_scope", "Namespace metadata is duplicate or pinned to another ontology version")
        if (raw_nodes or raw_edges) and not after:
            report.add("missing_scope", "Managed graph data has no namespace metadata")
        # Counts include malformed rows, not only successfully decoded ones.
        report.node_count, report.relationship_count = len(raw_nodes), len(raw_edges)
        report.nodes_by_class = dict(Counter(str(r["properties"].get("_kg_class", "<missing>")) for r in raw_nodes))
        report.relationships_by_type = dict(Counter(r["type"] for r in raw_edges))
        return report, nodes, edges

    def validate(self, tx, namespace, expected=None, *, exact=False):
        report, nodes, edges = self.snapshot(tx, namespace)
        if expected is not None:
            mapped = self.mapper.map(expected)
            if mapped.namespace != namespace:
                raise GraphInputError("Expected payload namespace differs from validation namespace")
            by_node, by_edge = {n.key: n for n in nodes}, {e.key: e for e in edges}
            report.expected_nodes, report.expected_relationships = len(mapped.nodes), len(mapped.edges)
            report.matched_nodes = sum(by_node.get(n.key) == n for n in mapped.nodes)
            report.matched_relationships = sum(by_edge.get(e.key) == e for e in mapped.edges)
            if (report.matched_nodes != report.expected_nodes or report.matched_relationships != report.expected_relationships):
                report.add("reconciliation", "Expected identities, revisions, properties or evidence do not match")
            if exact and (report.node_count != len(mapped.nodes) or report.relationship_count != len(mapped.edges)):
                report.add("unexpected_records", "Namespace contains records outside the expected payload")
        elif exact:
            raise GraphInputError("Exact reconciliation requires an expected payload")
        return report
