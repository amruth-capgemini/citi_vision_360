"""Offline result/transport contracts; mocks do not execute or validate Cypher."""

from copy import deepcopy
import json
from pathlib import Path
import re
from unittest.mock import Mock

import pytest

from citi_project.services.ontology import OntologyRegistry
from citi_project.services.knowledge_graph import EntityRef, GraphInputError, GraphMapper, GraphPayload
from citi_project.services.knowledge_graph.query_service import KnowledgeGraphQueryService
from citi_project.services.knowledge_graph import query_service as queries


@pytest.fixture
def setup():
    registry = OntologyRegistry.load()
    payload = GraphPayload.from_dict(json.loads(
        (Path(__file__).parent / "fixtures/knowledge_graph/representative.json").read_text(encoding="utf-8")))
    mapped = GraphMapper(registry).map(payload)
    tx, client = Mock(), Mock()
    client.read.side_effect = lambda callback: callback(tx)
    client.write.side_effect = AssertionError("Query service must never write")
    service = KnowledgeGraphQueryService(registry, client, namespace=payload.namespace)
    return service, client, tx, mapped


def node(mapped, class_id, identity=None, occurrence=None):
    return next(n for n in mapped.nodes if n.class_id == class_id
                and (identity is None or n.properties.get("_kg_identity") == identity)
                and (occurrence is None or n.properties.get("_kg_occurrence_id") == occurrence))


def paths(mapped, start, steps):
    """Build expected mock rows from fixture edges, independently of query strings."""
    nodes = {n.key: n for n in mapped.nodes}
    frontier = [([start.properties], [])]
    for relation, direction in steps:
        following = []
        for ns, rs in frontier:
            for edge in mapped.edges:
                near, far = (edge.source, edge.target) if direction == "out" else (edge.target, edge.source)
                if edge.type == relation and near == ns[-1]["_kg_key"]:
                    following.append((ns + [nodes[far].properties], rs + [{
                        "key": edge.key, "type": edge.type, "source": edge.source,
                        "target": edge.target, "properties": edge.properties}]))
        frontier = following
    return [{"nodes": ns, "relationships": rs} for ns, rs in frontier]


def test_vendor_contracts_and_transport_contract(setup):
    service, client, tx, mapped = setup
    vendor = node(mapped, "Vendor", "V-001")
    rows = paths(mapped, vendor, [("PARTY_TO", "out")])
    tx.run.side_effect = [[{"entity": vendor.properties}], rows + rows]
    result = service.get_vendor_contracts("V-001")
    assert result["root"]["identity"] == "V-001"
    assert len(result["contracts"]["items"]) == 1
    contract = result["contracts"]["items"][0]["nodes"][-1]
    assert contract["properties"]["contract_id"] == "CTR-001"
    assert contract["revision"] == 1
    assert all(not k.startswith("_kg_") for k in contract["properties"])
    client.read.assert_called_once()
    client.write.assert_not_called()
    for call in tx.run.call_args_list:
        assert call.kwargs["namespace"] == mapped.namespace
        assert call.kwargs["key"] == vendor.key
        assert "V-001" not in call.args[0]
    json.dumps(result)


def test_dependencies_preserve_support_portal_and_multiple_product_paths(setup):
    service, _, tx, mapped = setup
    contract = node(mapped, "Contract", "CTR-001")
    funded = [("HAS_SOW", "out"), ("FUNDS", "out")]
    steps = [funded[:1], funded, funded + [("SUPPORTS", "out")],
             funded + [("DEPENDS_ON", "out")], funded + [("SUPPORTS_PROCESS", "out")],
             funded + [("SUPPORTS", "out"), ("ENABLES", "out")]]
    tx.run.side_effect = [[{"entity": contract.properties}]] + [paths(mapped, contract, s) for s in steps]
    result = service.get_contract_dependencies("CTR-001")
    assert len(result["applications"]["items"]) == 2
    assert len(result["products"]["items"]) == 2  # distinct paths to the same product
    assert len({p["nodes"][-1]["key"] for p in result["products"]["items"]}) == 1
    contract = node(mapped, "Contract", "CTR-002")
    rows = paths(mapped, contract, funded + [("USES_PORTAL", "out")])
    tx.run.side_effect = [[{"entity": contract.properties}], [], [], rows, [], [], []]
    result = service.get_contract_dependencies("CTR-002")
    assert result["applications"]["items"][0]["relationships"][-1]["type"] == "USES_PORTAL"


def test_clauses_preserve_occurrences_and_key_properties(setup):
    service, _, tx, mapped = setup
    contract = node(mapped, "Contract", "CTR-001")
    rows = paths(mapped, contract, [("HAS_CLAUSE", "out")])
    tx.run.side_effect = [[{"entity": contract.properties}], rows]
    result = service.get_contract_clauses("CTR-001")
    clauses = [p["nodes"][-1] for p in result["clauses"]["items"]]
    assert len(clauses) == 4
    assert {c["class_id"] for c in clauses} == {"RenewalClause", "TerminationClause", "PaymentTermsClause"}
    assert {c["occurrence_id"] for c in clauses if c["class_id"] == "RenewalClause"} == {
        "renew1", "renew-support-extension"}
    assert next(c for c in clauses if c["occurrence_id"] == "renew1")["properties"]["notice_days"] == 30


def test_risk_sla_only_returns_stored_facts_and_direction(setup):
    service, _, tx, mapped = setup
    contract = node(mapped, "Contract", "CTR-001")
    funded = [("HAS_SOW", "out"), ("FUNDS", "out")]
    vendor = [("PARTY_TO", "in")]
    steps = [funded + [("HAS_SLA", "out")],
             funded + [("HAS_SLA", "out"), ("MEASURED_AGAINST", "in")],
             vendor + [("ASSESSES", "in")], vendor + [("ASSESSES", "in"), ("RAISED_IN", "in")],
             funded + [("ASSESSES", "in")], funded + [("ASSESSES", "in"), ("RAISED_IN", "in")],
             vendor + [("AFFECTS", "in")], funded + [("AFFECTS", "in")],
             funded + [("SUPPORTS", "out"), ("AFFECTS", "in")]]
    tx.run.side_effect = [[{"entity": contract.properties}]] + [paths(mapped, contract, s) for s in steps]
    result = service.get_contract_risk_and_sla("CTR-001")
    measurement = result["measurements"]["items"][0]
    assert measurement["nodes"][-1]["properties"]["actual"] == 99.0
    assert measurement["relationships"][-1]["source"] == measurement["nodes"][-1]["key"]
    assert result["application_affected_issues"]["items"][0]["nodes"][-1]["identity"] == "ISSUE-001"
    contract = node(mapped, "Contract", "CTR-002")
    tx.run.side_effect = [[{"entity": contract.properties}]] + [paths(mapped, contract, s) for s in steps]
    result = service.get_contract_risk_and_sla("CTR-002")
    assessment = result["vendor_assessments"]["items"][0]["nodes"][-1]
    assert assessment["properties"]["status"] == "Missing"
    assert "risk_tier" not in assessment["properties"]
    assert result["vendor_assessment_issues"]["items"] == []


@pytest.mark.parametrize("method,identity", [
    ("get_vendor_contracts", "V-001"), ("get_contract_dependencies", "CTR-001"),
    ("get_contract_clauses", "CTR-001"), ("get_contract_risk_and_sla", "CTR-001")])
def test_missing_root_short_circuits(setup, method, identity):
    service, client, tx, _ = setup
    tx.run.return_value = []
    result = getattr(service, method)(identity)
    assert result["root"] is None
    tx.run.assert_called_once()
    client.write.assert_not_called()


def test_existing_root_without_connections_and_limit(setup):
    service, _, tx, mapped = setup
    contract = node(mapped, "Contract", "CTR-001")
    tx.run.side_effect = [[{"entity": contract.properties}], []]
    assert service.get_contract_clauses("CTR-001")["clauses"] == {"items": [], "truncated": False}
    service.limit = 2
    tx.run.side_effect = [[{"entity": contract.properties}], paths(mapped, contract, [("HAS_CLAUSE", "out")])[:3]]
    result = service.get_contract_clauses("CTR-001")
    assert len(result["clauses"]["items"]) == 2 and result["clauses"]["truncated"]
    assert tx.run.call_args.kwargs["fetch_limit"] == 3


@pytest.mark.parametrize("relationship", [False, True])
def test_evidence_preserves_citations_and_resolves_documents(setup, relationship):
    service, client, tx, mapped = setup
    clause = node(mapped, "RenewalClause", occurrence="renew1")
    ref = EntityRef("RenewalClause", contract_identity="CTR-001", occurrence_id="renew1")
    edge = next(e for e in mapped.edges if e.type == "HAS_CLAUSE" and e.target == clause.key)
    props = edge.properties if relationship else clause.properties
    citations = json.loads(props["_kg_provenance"])
    document = node(mapped, "ContractDocument", citations[0]["document"]["identity"])
    responses = [[{"entity": props}], [{"entity": document.properties}]]
    if not relationship:
        responses.append(paths(mapped, clause, [("EVIDENCED_BY", "out")]))
    tx.run.side_effect = responses
    result = service.get_evidence_for_entity_or_relationship(**(
        {"relationship_type": "HAS_CLAUSE", "source": EntityRef("Contract", "CTR-001"), "target": ref}
        if relationship else {"entity": ref}))
    assert result["citations"]["items"] == citations
    assert {"document", "page", "section", "chunk_id", "evidence_ref", "review_state"} <= citations[0].keys()
    assert result["documents"]["items"][0]["key"] == document.key
    assert result["found"]
    assert bool(result["evidenced_by"]["items"]) is not relationship
    if relationship:
        assert tx.run.call_args_list[0].kwargs["key"] == edge.key
    client.write.assert_not_called()


def test_evidence_absent_missing_document_and_truncation(setup):
    service, _, tx, mapped = setup
    vendor = node(mapped, "Vendor", "V-001")
    ref = EntityRef("Vendor", "V-001")
    tx.run.return_value = []
    assert not service.get_evidence_for_entity_or_relationship(entity=ref)["found"]
    props = deepcopy(vendor.properties)
    props.pop("_kg_provenance")
    tx.run.side_effect = [[{"entity": props}], []]
    assert service.get_evidence_for_entity_or_relationship(entity=ref)["citations"]["items"] == []
    citation = json.loads(node(mapped, "RenewalClause", occurrence="renew1").properties["_kg_provenance"])[0]
    props["_kg_provenance"] = json.dumps([citation, {**citation, "page": 2}])
    service.limit = 1
    tx.run.side_effect = [[{"entity": props}], [], []]
    result = service.get_evidence_for_entity_or_relationship(entity=ref)
    assert result["citations"]["truncated"] and len(result["citations"]["items"]) == 1
    assert result["documents"]["items"] == []


@pytest.mark.parametrize("bad", ["{", "null", "{}", "[1]", '[{"document": {"class_id": "Vendor", "identity": "V-001"}}]'])
def test_malformed_evidence_is_not_silently_discarded(setup, bad):
    service, _, tx, _ = setup
    tx.run.return_value = [{"entity": {"_kg_provenance": bad}}]
    with pytest.raises(GraphInputError, match="Invalid stored provenance"):
        service.get_evidence_for_entity_or_relationship(entity=EntityRef("Vendor", "V-001"))


@pytest.mark.parametrize("kwargs", [{}, {"entity": EntityRef("Vendor", "V-001"), "relationship_type": "PARTY_TO"},
    {"relationship_type": "UNKNOWN", "source": EntityRef("Vendor", "V-001"), "target": EntityRef("Contract", "CTR-001")},
    {"relationship_type": "PARTY_TO", "source": EntityRef("Contract", "CTR-001"), "target": EntityRef("Vendor", "V-001")}])
def test_invalid_evidence_selector_does_not_connect(setup, kwargs):
    service, client, _, _ = setup
    with pytest.raises(GraphInputError):
        service.get_evidence_for_entity_or_relationship(**kwargs)
    client.read.assert_not_called()


@pytest.mark.parametrize("limit", [0, -1, 1001, True, 1.5])
def test_invalid_limits(setup, limit):
    service, client, _, _ = setup
    with pytest.raises(GraphInputError):
        KnowledgeGraphQueryService(service.registry, client, namespace=service.namespace, limit=limit)


def test_invalid_identity_and_namespace_do_not_connect(setup):
    service, client, _, _ = setup
    with pytest.raises(GraphInputError):
        service.get_contract_dependencies("CTR-001' MATCH (n) RETURN n")
    with pytest.raises(GraphInputError):
        KnowledgeGraphQueryService(service.registry, client, namespace=" ")
    client.read.assert_not_called()


def test_all_queries_are_bounded_read_only_and_namespace_scoped():
    path_queries = [queries._CONTRACTS, queries._CLAUSES, queries._EVIDENCED_BY,
                    *queries._DEPENDENCIES.values(), *queries._RISK_SLA.values()]
    for query in [queries._ROOT, queries._EDGE, queries._DOCUMENTS, *path_queries]:
        assert not re.search(r"\b(CREATE|MERGE|SET|DELETE|REMOVE|CALL|LOAD)\b", query)
        assert "LIMIT " in query and "$namespace" in query
        assert not re.search(r"\[.*\*", query)
    for query in path_queries:
        assert "all(n IN nodes(p) WHERE n._kg_namespace = $namespace)" in query
        assert "all(r IN relationships(p) WHERE r._kg_namespace = $namespace)" in query
        assert "WITH DISTINCT p" in query and "ORDER BY" in query
    assert queries._EDGE.count("_kg_namespace: $namespace") == 3
    assert "d:CitiKGEntity:Document" in queries._DOCUMENTS


def test_client_errors_propagate(setup):
    service, client, _, _ = setup
    client.read.side_effect = RuntimeError("read failed")
    with pytest.raises(RuntimeError, match="read failed"):
        service.get_vendor_contracts("V-001")
