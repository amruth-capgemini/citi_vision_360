"""Bounded business reads over existing ontology facts; no inferred relationships.

Results contain a root (None when absent) and named collections of explicit paths.
Each collection has its own limit and truncation flag. Nodes/edges within paths
are property maps, so the same result works with Bolt and the HTTPS Query API.
"""

import json

from ..ontology import OntologyValidationError
from .identity import canonical_identity, relationship_identity, text_id
from .models import EntityRef, GraphInputError
from .transport import GraphClient


_ROOT = """// citi-kg:query-root
MATCH (n:CitiKGEntity {_kg_key: $key, _kg_namespace: $namespace})
RETURN properties(n) AS entity LIMIT 1"""

_FUNDED = "(root)-[:HAS_SOW]->(:StatementOfWork)-[:FUNDS]->(service:Service)"
_VENDOR = "(root)<-[:PARTY_TO]-(vendor:Vendor)"
_APPLICATION = _FUNDED + "-[:SUPPORTS|USES_PORTAL]->(application:Application)"


def _path_query(pattern, condition="true"):
    # Only module-owned patterns enter this function; caller values are parameters.
    return """// citi-kg:query-paths
MATCH (root:CitiKGEntity {_kg_key: $key, _kg_namespace: $namespace})
MATCH p = """ + pattern + """
WHERE all(n IN nodes(p) WHERE n._kg_namespace = $namespace)
  AND all(r IN relationships(p) WHERE r._kg_namespace = $namespace)
  AND """ + condition + """
WITH DISTINCT p
RETURN [n IN nodes(p) | properties(n)] AS nodes,
       [r IN relationships(p) | {key: r._kg_key, type: type(r),
         source: startNode(r)._kg_key, target: endNode(r)._kg_key,
         properties: properties(r)}] AS relationships
ORDER BY [n IN nodes(p) | n._kg_key], [r IN relationships(p) | r._kg_key]
LIMIT $fetch_limit"""


_CONTRACTS = _path_query("(root)-[:PARTY_TO]->(:Contract)")
_DEPENDENCIES = {
    "statements_of_work": _path_query("(root)-[:HAS_SOW]->(:StatementOfWork)"),
    "services": _path_query(_FUNDED),
    "applications": _path_query(_APPLICATION),
    "configuration_items": _path_query(_FUNDED + "-[:DEPENDS_ON]->(:ConfigurationItem)"),
    "business_processes": _path_query(_FUNDED + "-[:SUPPORTS_PROCESS]->(:BusinessProcess)"),
    "products": _path_query(_APPLICATION + "-[:ENABLES]->(:Product)"),
}
_CLAUSES = _path_query("(root)-[:HAS_CLAUSE]->(clause:Clause)",
                       "clause._kg_class IN ['RenewalClause', 'TerminationClause', 'PaymentTermsClause']")
_RISK_SLA = {
    "slas": _path_query(_FUNDED + "-[:HAS_SLA]->(:ServiceLevelAgreement)"),
    "measurements": _path_query(_FUNDED + "-[:HAS_SLA]->(:ServiceLevelAgreement)"
                                "<-[:MEASURED_AGAINST]-(:PerformanceMeasurement)"),
}
for _name, _pattern in (("vendor", _VENDOR), ("service", _FUNDED)):
    _assessment = _pattern + "<-[:ASSESSES]-(:RiskAssessment)"
    _RISK_SLA[_name + "_assessments"] = _path_query(_assessment)
    _RISK_SLA[_name + "_assessment_issues"] = _path_query(_assessment + "<-[:RAISED_IN]-(:RiskIssue)")
for _name, _pattern in (("vendor", _VENDOR), ("service", _FUNDED), ("application", _APPLICATION)):
    _RISK_SLA[_name + "_affected_issues"] = _path_query(_pattern + "<-[:AFFECTS]-(:RiskIssue)")

_EDGE = """// citi-kg:query-evidence-edge
MATCH (s:CitiKGEntity {_kg_key: $source, _kg_namespace: $namespace})
      -[r {_kg_key: $key, _kg_namespace: $namespace}]->
      (t:CitiKGEntity {_kg_key: $target, _kg_namespace: $namespace})
WHERE type(r) = $relationship_type
RETURN properties(r) AS entity LIMIT 1"""
_DOCUMENTS = """// citi-kg:query-evidence-documents
UNWIND $keys AS key
MATCH (d:CitiKGEntity:Document {_kg_key: key, _kg_namespace: $namespace})
RETURN DISTINCT properties(d) AS entity ORDER BY entity._kg_key LIMIT $fetch_limit"""
_EVIDENCED_BY = _path_query("(root)-[:EVIDENCED_BY]->(:Document)")


def _entity(properties):
    return {
        "key": properties.get("_kg_key"), "class_id": properties.get("_kg_class"),
        "identity": properties.get("_kg_identity"),
        "contract_identity": properties.get("_kg_contract_identity"),
        "occurrence_id": properties.get("_kg_occurrence_id"),
        "revision": properties.get("_kg_revision"),
        "properties": {k: v for k, v in properties.items() if not k.startswith("_kg_")},
    }


class KnowledgeGraphQueryService:
    """Namespace-bound reads. Limits count distinct paths, not unique end nodes.

    A node reachable by different paths remains in each path, preserving the
    evidence of connectivity. Missing optional properties remain absent.
    Multi-statement reads use one client read callback, without snapshot guarantees.
    """

    def __init__(self, registry, client: GraphClient, *, namespace, limit=100):
        text_id(namespace, "namespace")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise GraphInputError("limit must be an integer between 1 and 1000")
        self.registry, self.client = registry, client
        self.namespace, self.limit = namespace, limit

    def _key(self, ref):
        return canonical_identity(self.registry, self.namespace, ref)

    def _run(self, tx, query, **params):
        return list(tx.run(query, namespace=self.namespace, fetch_limit=self.limit + 1, **params))

    def _paths(self, tx, query, key):
        rows = self._run(tx, query, key=key)
        paths, seen = [], set()
        for row in rows:
            signature = (tuple(n["_kg_key"] for n in row["nodes"]),
                         tuple(r["key"] for r in row["relationships"]))
            if signature in seen:
                continue
            seen.add(signature)
            paths.append({"nodes": [_entity(n) for n in row["nodes"]], "relationships": [
                {"key": r["key"], "type": r["type"], "source": r["source"], "target": r["target"],
                 "revision": r["properties"].get("_kg_revision"),
                 "properties": _entity(r["properties"])["properties"]}
                for r in row["relationships"]]})
        return {"items": paths[:self.limit], "truncated": len(paths) > self.limit}

    def _read(self, ref, queries):
        key = self._key(ref)

        def read(tx):
            rows = self._run(tx, _ROOT, key=key)
            return {"namespace": self.namespace, "root": _entity(rows[0]["entity"]) if rows else None,
                    "limit": self.limit, **{
                        name: self._paths(tx, query, key) if rows else {"items": [], "truncated": False}
                        for name, query in queries.items()}}
        return self.client.read(read)

    def get_vendor_contracts(self, vendor_id):
        return self._read(EntityRef("Vendor", vendor_id), {"contracts": _CONTRACTS})

    def get_contract_dependencies(self, contract_id):
        return self._read(EntityRef("Contract", contract_id), _DEPENDENCIES)

    def get_contract_clauses(self, contract_id):
        return self._read(EntityRef("Contract", contract_id), {"clauses": _CLAUSES})

    def get_contract_risk_and_sla(self, contract_id):
        return self._read(EntityRef("Contract", contract_id), _RISK_SLA)

    def get_evidence_for_entity_or_relationship(self, *, entity=None, relationship_type=None,
                                                source=None, target=None):
        """Select an EntityRef OR a directed relationship type and two EntityRefs.

        citations preserve the stored JSON fields verbatim; documents resolve only
        the returned citations. Explicit EVIDENCED_BY paths are separate facts.
        Invalid stored provenance raises GraphInputError rather than hiding evidence.
        """
        params = {}
        if entity is not None:
            if any(v is not None for v in (relationship_type, source, target)):
                raise GraphInputError("Select either an entity or a relationship")
            key, query = self._key(entity), _ROOT
        else:
            if relationship_type is None or source is None or target is None:
                raise GraphInputError("A relationship requires type, source and target")
            text_id(relationship_type, "relationship type")
            source_key, target_key = self._key(source), self._key(target)
            try:
                relation = self.registry.resolve_relation(relationship_type)
                self.registry.validate_relation(relation.id, source.class_id, target.class_id)
            except OntologyValidationError:
                raise GraphInputError("Unknown relationship or invalid directed endpoints") from None
            key = relationship_identity(self.namespace, relation.id, source_key, target_key)
            query = _EDGE
            params = {"source": source_key, "target": target_key, "relationship_type": relation.id}

        def read(tx):
            rows = self._run(tx, query, key=key, **params)
            result = {"namespace": self.namespace, "key": key, "found": bool(rows), "limit": self.limit,
                      "citations": {"items": [], "truncated": False},
                      "documents": {"items": [], "truncated": False},
                      "evidenced_by": {"items": [], "truncated": False}}
            if not rows:
                return result
            try:
                citations = json.loads(rows[0]["entity"].get("_kg_provenance", "[]"))
                if not isinstance(citations, list) or any(not isinstance(c, dict) for c in citations):
                    raise ValueError
                keys = set()
                for citation in citations[:self.limit]:
                    if citation.get("document") is not None:
                        ref = EntityRef(**citation["document"])
                        if self.registry.get_class(ref.class_id).kind != "document":
                            raise ValueError
                        keys.add(self._key(ref))
            except (ValueError, TypeError, OntologyValidationError):
                raise GraphInputError("Invalid stored provenance") from None
            result["citations"] = {"items": citations[:self.limit], "truncated": len(citations) > self.limit}
            if keys:
                documents = self._run(tx, _DOCUMENTS, keys=sorted(keys))
                result["documents"] = {"items": [_entity(r["entity"]) for r in documents[:self.limit]],
                                       "truncated": len(documents) > self.limit}
            if entity is not None:
                result["evidenced_by"] = self._paths(tx, _EVIDENCED_BY, key)
            return result
        return self.client.read(read)
