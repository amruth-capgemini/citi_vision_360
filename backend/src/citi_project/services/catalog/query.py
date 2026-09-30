"""Fixed, parameterized, namespace-filtered catalog reads. Callers never supply Cypher."""

import re

from ..knowledge_graph.models import GraphInputError
from .ontology import CATALOG_NAMESPACE, DOMAINS

_CONCEPT = re.compile(r"[A-Z][A-Za-z0-9]*(\.[a-z][a-z0-9_]*)?")
MAX_CONCEPTS = 50

FIND_SOURCES = """// citi-catalog:find-sources
UNWIND $concepts AS concept
MATCH (s:SourceSystem {_kg_namespace: $namespace})-[:PUBLISHES]->(d:Dataset {_kg_namespace: $namespace})
      -[:HAS_FIELD]->(f:DataField {_kg_namespace: $namespace})
WHERE f.ontology_class = concept.class AND (concept.property IS NULL OR f.ontology_property = concept.property)
  AND ($domain IS NULL OR EXISTS {
        MATCH (d)-[:IN_DOMAIN]->(:BusinessDomain {_kg_namespace: $namespace, domain_id: $domain}) })
WITH concept, s, d, f ORDER BY f.position
RETURN concept.name AS concept, d.dataset_id AS dataset_id, d.physical_name AS physical_name,
       s.source_system_id AS system, d.as_of AS as_of, d.row_count AS row_count, d.review_state AS dataset_review_state,
       collect({field: f.path, business_name: f.business_name, mapping: f.ontology_class + '.' + f.ontology_property, role: f.semantic_role,
                confidence: f.confidence, review_state: f.review_state}) AS fields
ORDER BY concept, dataset_id
LIMIT $limit"""

DATASET = """// citi-catalog:describe-dataset
MATCH (s:SourceSystem {_kg_namespace: $namespace})-[:PUBLISHES]->(d:Dataset {_kg_namespace: $namespace, dataset_id: $dataset_id})
OPTIONAL MATCH (d)-[r:IN_DOMAIN]->(b:BusinessDomain {_kg_namespace: $namespace})
WITH s, d, collect(DISTINCT {domain: b.domain_id, basis: r.basis}) AS domains
OPTIONAL MATCH (d)-[:HAS_RECORDS_FOR]->(a:ContractAnchor {_kg_namespace: $namespace})
RETURN properties(s) AS system, properties(d) AS dataset, domains, count(DISTINCT a) AS contract_count"""

FIELDS = """// citi-catalog:describe-fields
MATCH (d:Dataset {_kg_namespace: $namespace, dataset_id: $dataset_id})-[:HAS_FIELD]->(f:DataField {_kg_namespace: $namespace})
OPTIONAL MATCH (f)-[r:REFERENCES]->(key:DataField {_kg_namespace: $namespace})
RETURN properties(f) AS field, key.field_id AS references, r.review_state AS reference_review_state
ORDER BY f.position LIMIT $limit"""

KEY_CONCEPT = """// citi-catalog:key-concept
MATCH (c:Concept {_kg_namespace: $namespace})
WHERE c.concept_id STARTS WITH $prefix AND c.is_key
RETURN c.concept_id AS concept_id LIMIT 1"""

JOIN_PLAN = """// citi-catalog:join-plan
MATCH (:Concept {_kg_namespace: $namespace, concept_id: $concept})<-[:MAPS_TO]-(key:DataField {_kg_namespace: $namespace})
      <-[:HAS_FIELD]-(home:Dataset {_kg_namespace: $namespace})
WHERE EXISTS { MATCH (key)<-[:REFERENCES]-(:DataField {_kg_namespace: $namespace}) }
MATCH (child:DataField {_kg_namespace: $namespace})-[r:REFERENCES]->(key)
MATCH (d:Dataset {_kg_namespace: $namespace})-[:HAS_FIELD]->(child)
WITH home, key, d, child, r ORDER BY d.dataset_id, child.position
RETURN home.dataset_id AS home_dataset, key.path AS key_field, home.grain AS home_grain, home.row_count AS home_rows,
       collect({dataset: d.dataset_id, field: child.path, business_name: child.business_name, grain: d.grain,
                primary_key: d.primary_key, row_count: d.row_count, basis: r.basis, confidence: r.confidence,
                review_state: r.review_state}) AS joins
LIMIT $limit"""

FOR_CONTRACT = """// citi-catalog:datasets-for-contract
MATCH (s:SourceSystem {_kg_namespace: $namespace})-[:PUBLISHES]->(d:Dataset {_kg_namespace: $namespace})
      -[r:HAS_RECORDS_FOR]->(:ContractAnchor {_kg_namespace: $namespace, contract_id: $contract_id})
RETURN d.dataset_id AS dataset_id, d.physical_name AS physical_name, s.source_system_id AS system,
       r.via_field AS via_field, d.as_of AS as_of, d.review_state AS review_state
ORDER BY dataset_id LIMIT $limit"""

OVERVIEW = """// citi-catalog:overview
MATCH (s:SourceSystem {_kg_namespace: $namespace})-[:PUBLISHES]->(d:Dataset {_kg_namespace: $namespace})
OPTIONAL MATCH (d)-[:HAS_FIELD]->(f:DataField {_kg_namespace: $namespace})
WITH s, d, count(f) AS fields, count(f.ontology_class) AS mapped,
     sum(CASE WHEN f.review_state = 'auto' THEN 1 ELSE 0 END) AS auto,
     sum(CASE WHEN f.review_state = 'unreviewed' THEN 1 ELSE 0 END) AS unreviewed,
     sum(CASE WHEN f.name_collision THEN 1 ELSE 0 END) AS collisions
OPTIONAL MATCH (d)-[:IN_DOMAIN]->(b:BusinessDomain {_kg_namespace: $namespace})
WITH s, d, fields, mapped, auto, unreviewed, collisions, collect(DISTINCT b.domain_id) AS domains
OPTIONAL MATCH (d)-[:HAS_RECORDS_FOR]->(a:ContractAnchor {_kg_namespace: $namespace})
RETURN s.source_system_id AS system, d.dataset_id AS dataset_id, d.row_count AS row_count, d.grain AS grain,
       d.review_state AS review_state, domains, fields, mapped, auto, unreviewed, collisions,
       count(DISTINCT a) AS contracts
ORDER BY dataset_id LIMIT $limit"""


SCHEMA_INDEX = """// citi-catalog:schema-index
MATCH (d:Dataset {_kg_namespace: $namespace})-[:HAS_FIELD]->(f:DataField {_kg_namespace: $namespace})
OPTIONAL MATCH (f)-[:MAPS_TO]->(c:Concept {_kg_namespace: $namespace})
OPTIONAL MATCH (f)-[r:REFERENCES]->(k:DataField {_kg_namespace: $namespace})
WITH d, f, c, r, k ORDER BY d.dataset_id, f.position
RETURN d.dataset_id AS dataset_id, d.grain AS grain, d.row_count AS row_count, d.primary_key AS primary_key,
       d.description AS description,
       collect({name: f.path, business_name: f.business_name, concept: c.concept_id, role: f.semantic_role,
                masked: f.masked, references: k.field_id, basis: r.basis, review_state: r.review_state}) AS fields
ORDER BY dataset_id LIMIT $limit"""


def _public(properties):
    return {k: v for k, v in (properties or {}).items() if not k.startswith("_kg_")}


class CatalogQueryService:
    def __init__(self, client, namespace=CATALOG_NAMESPACE, *, limit=500):
        if type(limit) is not int or not 1 <= limit <= 5000:
            raise ValueError("limit must be between 1 and 5000")
        self.client, self.namespace, self.limit = client, namespace, limit

    def _read(self, query, **params):
        return self.client.read(lambda tx: [dict(r) for r in tx.run(query, namespace=self.namespace, limit=self.limit, **params)])

    def find_sources(self, concepts, domain=None):
        """Datasets whose fields carry each "Class" or "Class.property" concept, with freshness and review state."""
        if (not isinstance(concepts, (list, tuple)) or not concepts or len(concepts) > MAX_CONCEPTS
                or any(not isinstance(c, str) or not _CONCEPT.fullmatch(c) for c in concepts)):
            raise GraphInputError("Concepts must be 1-50 'Class' or 'Class.property' names")
        if domain is not None and domain not in DOMAINS:
            raise GraphInputError("Unknown business domain")
        unique = list(dict.fromkeys(concepts))
        rows = self._read(FIND_SOURCES, domain=domain, concepts=[
            {"name": c, "class": c.split(".")[0], "property": c.split(".", 1)[1] if "." in c else None} for c in unique])
        result = {c: [] for c in unique}
        for row in rows:
            result[row.pop("concept")].append(row)
        return {"namespace": self.namespace, "sources": result, "missing": [c for c in unique if not result[c]]}

    def describe_dataset(self, dataset_id):
        if not isinstance(dataset_id, str) or not re.fullmatch(r"[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*", dataset_id):
            raise GraphInputError("dataset_id must be schema.table")
        rows = self._read(DATASET, dataset_id=dataset_id)
        if not rows:
            return None
        row = rows[0]
        fields = self._read(FIELDS, dataset_id=dataset_id)
        return {"system": _public(row["system"]), "dataset": _public(row["dataset"]),
                "domains": sorted((d for d in row["domains"] if d["domain"]), key=lambda d: d["domain"]),
                "linked_contracts": row["contract_count"],
                "fields": [{**_public(f["field"]), "references": f["references"],
                            "reference_review_state": f["reference_review_state"]} for f in fields]}

    def join_plan(self, concept):
        """Where the concept's entity is keyed, and every dataset that joins to that key.

        For "Contract.end_date" this returns the home of Contract.contract_id and each
        dataset whose field references it, with the join field, grain and review state, so a
        caller can fetch and aggregate each dataset separately before combining.
        """
        if not isinstance(concept, str) or not _CONCEPT.fullmatch(concept):
            raise GraphInputError("Concept must be a 'Class' or 'Class.property' name")
        keys = self._read(KEY_CONCEPT, prefix=concept.split(".")[0] + ".")
        if not keys:
            return {"concept": concept, "key_concept": None, "homes": [], "carriers": []}
        key = keys[0]["concept_id"]
        homes = self._read(JOIN_PLAN, concept=key)
        carriers = self.find_sources([concept])["sources"][concept]
        return {"concept": concept, "key_concept": key, "homes": homes, "carriers": carriers}

    def datasets_for_contract(self, contract_id):
        if not isinstance(contract_id, str) or not re.fullmatch(r"CTR-\d{3}", contract_id):
            raise GraphInputError("Expected a canonical contract ID")
        return self._read(FOR_CONTRACT, contract_id=contract_id)

    def overview(self):
        return self._read(OVERVIEW)

    def schema_index(self):
        """Every dataset with its fields, concepts, masking and inferred foreign keys (for the agents' query guard)."""
        return self._read(SCHEMA_INDEX)
