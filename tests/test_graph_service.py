"""Offline transactional driver double; live Cypher is covered by the opt-in profile."""

from copy import deepcopy
from dataclasses import replace
import re

import pytest

from citi_project.services.ontology import OntologyRegistry
from citi_project.services.knowledge_graph import EntityInstance, EntityRef, Evidence, GraphInputError, GraphPayload, RelationshipInstance
from citi_project.services.knowledge_graph.cli import sample_payload
from citi_project.services.knowledge_graph.schema import schema_rules
from citi_project.services.knowledge_graph.service import KnowledgeGraphService


class Result(list):
    def single(self):
        return self[0] if self else None

    def consume(self):
        return None


class MemoryTransaction:
    """Models transaction atomicity and MERGE storage, not Neo4j query execution."""

    def __init__(self, client, state):
        self.client, self.state = client, state

    def run(self, query, **parameters):
        self.client.calls.append((query, deepcopy(parameters)))
        if query.startswith("SHOW CONSTRAINTS"):
            return Result(self.client.constraints)
        if "citi-kg:lock-scope" in query:
            version = self.state["scopes"].setdefault(parameters["namespace"], parameters["version"])
            return Result([{"version": version}])
        if "citi-kg:read-scope" in query:
            version = self.state["scopes"].get(parameters["namespace"])
            return Result([{"version": version, "revision": 0}]) if version is not None else Result()
        if "citi-kg:read-nodes" in query:
            return Result(deepcopy([n for n in self.state["nodes"].values()
                                    if n["properties"].get("_kg_namespace") == parameters["namespace"]]))
        if "citi-kg:read-edges" in query:
            rows = []
            for e in self.state["edges"].values():
                row = deepcopy(e)
                source = self.state["nodes"].get(row["source"], {}).get("properties", {})
                target = self.state["nodes"].get(row["target"], {}).get("properties", {})
                row["source_namespace"] = source.get("_kg_namespace")
                row["target_namespace"] = target.get("_kg_namespace")
                if parameters["namespace"] in (row["properties"].get("_kg_namespace"), row["source_namespace"], row["target_namespace"]):
                    rows.append(row)
            return Result(rows)
        if "citi-kg:upsert-nodes" in query:
            assert "MERGE (n:CitiKGEntity {_kg_key: row.key, _kg_namespace: $namespace})" in query
            labels = ["CitiKGEntity", *re.findall(r"`([^`]+)`", query)]
            for row in parameters["rows"]:
                self.state["nodes"][row["key"]] = {"labels": labels, "properties": deepcopy(row["properties"])}
            return Result([{"count": len(parameters["rows"])}])
        if "citi-kg:upsert-edges" in query:
            if self.client.fail_edges:
                raise GraphInputError("Simulated relationship write failure")
            assert "MERGE (s)-[r:" in query and "row.key" in query
            type_id = re.search(r"r:`([^`]+)`", query)[1]
            count = 0
            for row in parameters["rows"]:
                if row["source"] in self.state["nodes"] and row["target"] in self.state["nodes"]:
                    self.state["edges"][row["key"]] = {"type": type_id, "source": row["source"], "target": row["target"],
                                                       "properties": deepcopy(row["properties"])}
                    count += 1
            return Result([{"count": 0 if self.client.bad_count else count}])
        if query.startswith("CREATE "):
            return Result()
        raise AssertionError("Unexpected query")


class MemoryClient:
    def __init__(self, registry):
        self.state = {"nodes": {}, "edges": {}, "scopes": {}}
        self.constraints = [{"name": r.name, "type": r.type, "labelsOrTypes": [r.label], "properties": list(r.properties)}
                            for r in schema_rules(registry) if r.type != "INDEX"]
        self.calls = []
        self.fail_edges = False
        self.bad_count = False

    def write(self, callback, *args):
        state = deepcopy(self.state)
        result = callback(MemoryTransaction(self, state), *args)
        self.state = state  # exceptions leave the old state intact
        return result

    def read(self, callback, *args):
        before = deepcopy(self.state)
        result = callback(MemoryTransaction(self, self.state), *args)
        assert self.state == before
        return result


@pytest.fixture
def setup_graph():
    registry = OntologyRegistry.load()
    client = MemoryClient(registry)
    return KnowledgeGraphService(registry, client, batch_size=2), client, GraphPayload.from_dict(sample_payload(registry))


def test_atomic_ingestion_replay_and_reconciliation(setup_graph):
    service, client, payload = setup_graph
    first = service.ingest(payload)
    assert first.total_entities == 4 and first.total_relationships == 4
    assert first.written_entities == 4 and first.written_relationships == 4
    second = service.ingest(replace(payload, entities=tuple(reversed(payload.entities)), relationships=tuple(reversed(payload.relationships))))
    assert second.written_entities == second.written_relationships == 0
    assert second.total_entities == second.total_relationships == 4
    report = service.validate(payload.namespace, payload, exact=True)
    assert report.valid and report.matched_nodes == 4 and report.matched_relationships == 4
    assert len(client.state["nodes"]) == len(client.state["edges"]) == 4


def test_clause_occurrences_rerun_and_update_persist_correctly(setup_graph):
    service, client, payload = setup_graph
    clause = payload.entities[-1]
    second_clause = replace(clause, occurrence_id="renewal-section-2")
    service.ingest(replace(payload, entities=(*payload.entities, second_clause)))
    assert len(client.state["nodes"]) == 5
    service.upsert_entity(payload.namespace, clause)
    updated = replace(clause, properties={**clause.properties, "section_title": "Updated heading"}, revision=2)
    service.upsert_entity(payload.namespace, updated)
    assert len(client.state["nodes"]) == 5
    assert service.validate(payload.namespace).valid
    other_contract = replace(payload.entities[1], properties={**payload.entities[1].properties, "contract_id": "CTR-002"})
    other_clause = replace(clause, contract_identity="CTR-002")
    service.upsert_entities(payload.namespace, [other_contract, other_clause])
    assert len(client.state["nodes"]) == 7


def test_node_update_removes_optional_properties_and_rejects_stale_revision(setup_graph):
    service, client, payload = setup_graph
    vendor = replace(payload.entities[0], properties={**payload.entities[0].properties, "country": "US"})
    service.ingest(replace(payload, entities=(vendor, *payload.entities[1:])))
    changed = replace(payload.entities[0], revision=2)
    service.upsert_entity(payload.namespace, changed)
    node = next(n for n in client.state["nodes"].values() if n["properties"]["_kg_class"] == "Vendor")
    assert "country" not in node["properties"]
    before = deepcopy(client.state)
    with pytest.raises(GraphInputError, match="newer revision"):
        service.upsert_entity(payload.namespace, vendor)
    assert client.state == before


@pytest.mark.parametrize("failure", ["fail_edges", "bad_count"])
def test_batch_write_failure_rolls_back(setup_graph, failure):
    service, client, payload = setup_graph
    setattr(client, failure, True)
    before = deepcopy(client.state)
    with pytest.raises(GraphInputError):
        service.ingest(payload)
    assert client.state == before


def test_missing_endpoints_and_document_references_rejected(setup_graph):
    service, client, payload = setup_graph
    with pytest.raises(GraphInputError):
        service.upsert_relationships(payload.namespace, payload.relationships)
    with pytest.raises(GraphInputError):
        service.upsert_entity(payload.namespace, payload.entities[-1])
    assert not client.state["nodes"]
    # Contract exists but the evidence document is missing.
    service.upsert_entity(payload.namespace, payload.entities[1])
    with pytest.raises(GraphInputError):
        service.upsert_entity(payload.namespace, payload.entities[-1])


def test_scope_isolation_and_parameterization(setup_graph):
    service, client, payload = setup_graph
    injection = "x' MATCH (n) DETACH DELETE n //"
    vendor = replace(payload.entities[0], properties={**payload.entities[0].properties, "legal_name": injection})
    service.upsert_entity(injection, vendor)
    service.upsert_entity("other", vendor)
    assert len(client.state["nodes"]) == 2
    assert all(injection not in q for q, params in client.calls)
    assert any(injection == params.get("namespace") for q, params in client.calls)
    assert service.validate("other").node_count == 1


def test_schema_and_mapping_failures_happen_before_writes(setup_graph):
    service, client, payload = setup_graph
    with pytest.raises(GraphInputError):
        service.upsert_entity("test", EntityInstance("Service", {}))
    assert client.calls == []
    client.constraints = []
    with pytest.raises(GraphInputError, match="init-schema"):
        service.ingest(payload)
    assert not client.state["nodes"]


def test_namespace_ontology_version_pinned(setup_graph):
    service, client, payload = setup_graph
    client.state["scopes"][payload.namespace] = "old-version"
    with pytest.raises(GraphInputError, match="different ontology"):
        service.ingest(payload)


def test_disjoint_and_cardinality_against_existing_graph(setup_graph):
    service, client, payload = setup_graph
    namespace = "dependencies"
    nodes = [EntityInstance("Service", {"service_id": "SVC-001", "service_name": "Service"}),
             EntityInstance("Application", {"application_id": "APP-001", "name": "Application", "criticality": "Critical"}),
             EntityInstance("Owner", {"owner_id": "OWN-001", "role": "Owner"}),
             EntityInstance("Owner", {"owner_id": "OWN-002", "role": "Other owner"})]
    supports = RelationshipInstance("SUPPORTS", EntityRef("Service", "SVC-001"), EntityRef("Application", "APP-001"))
    owner = RelationshipInstance("OWNED_BY", supports.source, EntityRef("Owner", "OWN-001"))
    service.ingest(GraphPayload(namespace, service.registry.ontology_version, tuple(nodes), (supports, owner)))
    for bad in (replace(supports, type="USES_PORTAL"), replace(owner, target=EntityRef("Owner", "OWN-002"))):
        with pytest.raises(GraphInputError, match="constraints"):
            service.upsert_relationship(namespace, bad)
    assert len(client.state["edges"]) == 2


def test_relationship_updates_and_single_batch_apis(setup_graph):
    service, client, payload = setup_graph
    vendor = payload.entities[0]
    other = replace(vendor, properties={**vendor.properties, "vendor_id": "V-002"})
    service.upsert_entities("test", [vendor, other])
    rel = RelationshipInstance("POTENTIAL_ALTERNATIVE_TO", EntityRef("Vendor", "V-001"), EntityRef("Vendor", "V-002"),
                               {"validation_status": "Unvalidated"})
    service.upsert_relationship("test", rel)
    service.upsert_relationship("test", replace(rel, revision=2, properties={"validation_status": "Validated"}))
    assert len(client.state["edges"]) == 1


def test_validation_is_read_only_and_detects_corruption(setup_graph):
    service, client, payload = setup_graph
    service.ingest(payload)
    first = next(iter(client.state["nodes"].values()))
    first["properties"]["unmodelled_property"] = "unexpected"
    before = deepcopy(client.state)
    report = service.validate(payload.namespace, payload)
    assert not report.valid
    assert "invalid_node" in {i.code for i in report.issues}
    assert client.state == before


def test_reconciliation_compares_values_not_only_counts(setup_graph):
    service, client, payload = setup_graph
    service.ingest(payload)
    changed = replace(payload.entities[0], properties={**payload.entities[0].properties, "legal_name": "Different name"})
    expected = replace(payload, entities=(changed, *payload.entities[1:]))
    report = service.validate(payload.namespace, expected, exact=True)
    assert report.node_count == report.expected_nodes == 4
    assert report.matched_nodes == 3 and not report.valid


def test_schema_generation_uses_registry_and_is_idempotent(setup_graph):
    service, client, payload = setup_graph
    rules = schema_rules(service.registry)
    assert len({r.name for r in rules}) == len(rules)
    assert all("IF NOT EXISTS" in r.query for r in rules)
    assert len([r for r in rules if r.type == "RELATIONSHIP_UNIQUENESS"]) == len(service.registry.relations)
    assert any(r.label == "ContractDocument" and "document_id" in r.properties for r in rules)
    assert not any(r.label == "RenewalClause" for r in rules)  # canonical key handles occurrences
    assert service.ensure_schema() == service.ensure_schema() == len(rules)


def test_wrong_clause_contract_link_rejected(setup_graph):
    service, client, payload = setup_graph
    rel = payload.relationships[1]
    with pytest.raises(GraphInputError, match="identity contract"):
        service.mapper.edge(payload.namespace, replace(rel, source=EntityRef("Contract", "CTR-002")))


def test_cypher25_constraint_names_and_wrong_signature(setup_graph):
    service, client, payload = setup_graph
    for rule in client.constraints:
        rule["type"] = {"UNIQUENESS": "NODE_PROPERTY_UNIQUENESS",
                        "RELATIONSHIP_UNIQUENESS": "RELATIONSHIP_PROPERTY_UNIQUENESS"}[rule["type"]]
    assert service.ingest(payload).total_entities == 4
    client.constraints[0]["properties"] = ["wrong_key"]
    with pytest.raises(GraphInputError, match="init-schema"):
        service.ingest(payload)


@pytest.mark.parametrize("corruption,code", [
    ("duplicate_node", "duplicate_node"), ("duplicate_relationship", "duplicate_relationship"),
    ("required", "invalid_node"), ("unknown_type", "invalid_relationship"),
    ("wrong_direction", "invalid_relationship"), ("missing_endpoint", "missing_endpoint"),
])
def test_read_only_validation_reports_integrity_failures(setup_graph, corruption, code):
    service, client, payload = setup_graph
    service.ingest(payload)
    nodes, edges = client.state["nodes"], client.state["edges"]
    vendor_key = next(k for k, n in nodes.items() if n["properties"]["_kg_class"] == "Vendor")
    edge = next(e for e in edges.values() if e["type"] == "PARTY_TO")
    if corruption == "duplicate_node":
        nodes["test-duplicate"] = deepcopy(nodes[vendor_key])
    elif corruption == "duplicate_relationship":
        edges["test-duplicate"] = deepcopy(edge)
    elif corruption == "required":
        del nodes[vendor_key]["properties"]["legal_name"]
    elif corruption == "unknown_type":
        edge["type"] = "UNDECLARED"
    elif corruption == "wrong_direction":
        edge["source"], edge["target"] = edge["target"], edge["source"]
    elif corruption == "missing_endpoint":
        del nodes[vendor_key]
    before = deepcopy(client.state)
    report = service.validate(payload.namespace)
    assert not report.valid and code in {i.code for i in report.issues}
    assert client.state == before


def test_deduplicated_counts_and_record_limit(setup_graph):
    service, client, payload = setup_graph
    result = service.ingest(replace(payload, entities=payload.entities * 2, relationships=payload.relationships * 2))
    assert result.input_entities == result.input_relationships == 8
    assert result.unique_entities == result.unique_relationships == 4
    service.validator.max_records = 3
    with pytest.raises(GraphInputError, match="limit"):
        service.ingest(payload)


def test_read_only_validation_detects_concurrent_managed_write(setup_graph):
    service, client, payload = setup_graph
    service.ingest(payload)
    tx = MemoryTransaction(client, client.state)
    original = tx.run
    counter = 0

    def changed_scope(query, **kwargs):
        nonlocal counter
        result = original(query, **kwargs)
        if "citi-kg:read-scope" in query:
            counter += 1
            result[0]["revision"] = counter
        return result

    tx.run = changed_scope
    report = service.validator.validate(tx, payload.namespace)
    assert "concurrent_write" in {i.code for i in report.issues}
