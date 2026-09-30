"""Offline public graph API doubles grounded in the canonical synthetic pack.

These tests do not validate Cypher execution. Citation sentinels are explicitly
mock evidence, not invented pages attributed to the source PDFs.
"""

from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from citi_project.services.knowledge_graph.query_service import KnowledgeGraphQueryService
from citi_project.services.semantic_service import VendorSemanticService, calculate_sla_breach
from citi_project.services.structured_data import StructuredDataError, StructuredQueryService

DATA = Path(__file__).resolve().parents[1] / "initial_plan"
PACK = DATA / "Synthetic_20_Vendor_Six_Source_Data_Pack (3)" / "data"


def node(class_id, identity, properties=None):
    return {"key": f"{class_id}:{identity}", "class_id": class_id, "identity": identity,
            "contract_identity": None, "occurrence_id": None, "revision": 1, "properties": properties or {}}


def path(nodes, types):
    return {"nodes": nodes, "relationships": [
        {"key": f'{a["key"]}/{t}/{b["key"]}', "type": t, "source": a["key"], "target": b["key"], "revision": 1, "properties": {}}
        for a, b, t in zip(nodes, nodes[1:], types)]}


def collection(items=()):
    return {"items": list(items), "truncated": False}


def canonical_graph(structured):
    graph = Mock(spec=KnowledgeGraphQueryService)
    graph.namespace = structured.namespace

    def response(method, identity):
        vendor_id = identity if identity.startswith("V-") else "V-" + identity[-3:]
        master = structured.get_vendor_contract(vendor_id)["facts"]["contract"]
        vendor = node("Vendor", vendor_id)
        contract = node("Contract", master["Contract_ID"])
        sow = node("StatementOfWork", master["SOW_ID"])
        service = node("Service", master["Service_ID"])
        base = {"namespace": structured.namespace, "root": vendor if method == "get_vendor_contracts" else contract, "limit": 100}
        funded = [contract, sow, service]
        if method == "get_vendor_contracts":
            base["contracts"] = collection([path([vendor, contract], ["PARTY_TO"])])
        elif method == "get_contract_dependencies":
            base["statements_of_work"] = collection([path([contract, sow], ["HAS_SOW"])])
            base["services"] = collection([path(funded, ["HAS_SOW", "FUNDS"])])
            base["applications"] = collection([path(funded + [node("Application", row["Application_ID"])], ["HAS_SOW", "FUNDS", row["Relationship_Type"]]) for row in structured.get_vendor_application_bridge(vendor_id)["facts"]["applications"]])
        elif method == "get_contract_clauses":
            base["clauses"] = collection()
        else:
            source = json.loads((PACK / "06_risk_sla_performance" / f"{vendor_id}_risk_sla.json").read_text(encoding="utf-8"))
            sla = node("ServiceLevelAgreement", source["sla"]["sla_id"], source["sla"])
            assessment = node("RiskAssessment", source["risk_assessment"]["assessment_id"], source["risk_assessment"])
            base["slas"] = collection([path(funded + [sla], ["HAS_SOW", "FUNDS", "HAS_SLA"])])
            assessment_path = path(funded + [assessment], ["HAS_SOW", "FUNDS", "ASSESSES"])
            # ASSESSES is directed from assessment to service.
            edge = assessment_path["relationships"][-1]
            edge["source"], edge["target"] = edge["target"], edge["source"]
            base["service_assessments"] = collection([assessment_path])
            base["measurements"] = collection([path(funded + [sla, node("PerformanceMeasurement", p["metric_record_id"], p)], ["HAS_SOW", "FUNDS", "HAS_SLA", "MEASURED_AGAINST"]) for p in source["performance"]])
            for measurement_path in base["measurements"]["items"]:
                edge = measurement_path["relationships"][-1]
                edge["source"], edge["target"] = edge["target"], edge["source"]
        return deepcopy(base)

    for name in ("get_vendor_contracts", "get_contract_dependencies", "get_contract_clauses", "get_contract_risk_and_sla"):
        getattr(graph, name).side_effect = lambda identity, method=name: response(method, identity)
    document = node("Document", "offline-test-document")
    graph.get_evidence_for_entity_or_relationship.return_value = {
        "namespace": structured.namespace, "found": True, "key": "offline-test-evidence", "limit": 100,
        "citations": collection([{"document": {"class_id": "Document", "identity": "offline-test-document"},
                                  "evidence_ref": "synthetic:offline-test-sentinel", "review_state": "unreviewed", "verification_state": "unverified"}]),
        "documents": collection([document]), "evidenced_by": collection(),
    }
    return graph


@pytest.fixture
def setup():
    structured = StructuredQueryService(DATA)
    graph = canonical_graph(structured)
    return VendorSemanticService(structured, graph), graph


def codes(result):
    return {n["code"] for n in result["limitations"]}


def test_twelve_business_calls(setup):
    service, graph = setup
    assert len(service.get_contract_expiry_context()["facts"]["contracts"]) == 6
    assert service.get_vendor_financial_context("V-005")["facts"]["forecast_variance_amount"] == "294920.56"
    assert service.get_vendor_financial_context("V-009")["facts"]["actual_ytd_variance_amount"] == "399452.04"
    assert service.get_vendor_workforce_context("V-001")["facts"]["representative_workforce_count"] == 4
    assert {a["application_id"] for a in service.get_vendor_dependency_context("V-009")["facts"]["applications"]} == {"APP-001", "APP-003", "APP-008", "APP-010"}
    assert service.get_vendor_dependency_context("V-001")["facts"]["services"] == ["SVC-001"]
    assert service.get_vendor_risk_sla_context("V-001")["facts"]["has_sla_breach"] is True
    assert service.get_vendor_risk_sla_context("V-009")["facts"]["risk_status"] == "Stale"
    assert "risk_missing" in codes(service.get_vendor_risk_sla_context("V-017"))
    one = service.get_vendor_renewal_context("V-001")
    five = service.get_vendor_renewal_context("V-005")
    assert (one["facts"]["days_to_expiry"], five["facts"]["days_to_expiry"]) == (90, 15)
    assert one["facts"]["forecast"] == "10120547.96"
    combined = service.get_vendor_dependency_workforce_context("V-009")
    assert len(combined["facts"]["applications"]) == combined["facts"]["representative_workforce_count"] == 4
    json.dumps([one, five, combined])
    graph.get_contract_clauses.assert_any_call("CTR-001")
    graph.get_contract_clauses.assert_any_call("CTR-005")


@pytest.mark.parametrize("vendor,status,date,tier", [("V-009", "Stale", "2025-06-01", "High"), ("V-017", "Missing", None, None)])
def test_risk_source_truth(setup, vendor, status, date, tier):
    result = setup[0].get_vendor_risk_sla_context(vendor)
    assessment = result["facts"]["risk_assessments"][0]
    assert (assessment["status"], assessment["assessment_date"], assessment["risk_tier"]) == (status, date, tier)
    assert "risk_" + status.lower() in codes(result)


@pytest.mark.parametrize("actual,target,direction,expected", [(99, 99.9, "higher_is_better", True), (99.9, 99.9, "higher_is_better", False), (100, 99.9, "higher_is_better", False), (16, 15, "lower_is_better", True), (15, 15, "lower_is_better", False), (14, 15, "lower_is_better", False), (None, 99, "higher_is_better", None), (99, 99, "unknown", None), ("NaN", 99, "higher_is_better", None)])
def test_sla_direction_and_unknown(actual, target, direction, expected):
    assert calculate_sla_breach(actual, target, direction) is expected


def test_evidence_preserved_without_manufacturing_pages(setup):
    result = setup[0].get_vendor_dependency_context("V-001")
    docs = [e for e in result["evidence"] if e["source_type"] == "document"]
    assert docs and all(e["evidence_ref"] == "synthetic:offline-test-sentinel" and "page" not in e for e in docs)
    assert any(e["source_type"] == "neo4j" and e["relationship"] == "SUPPORTS" and e["source"] == "SVC-001" for e in result["evidence"])
    assert {"synthetic_data", "unverified_provenance", "synthetic_provenance"} <= codes(result)


def test_missing_graph_is_not_negative_finding():
    result = VendorSemanticService(StructuredQueryService(DATA)).get_vendor_renewal_context("V-001")
    assert result["facts"]["has_sla_breach"] is None
    assert result["facts"]["risk_status"] is None
    assert result["facts"]["graph_dependencies_available"] is False
    assert result["facts"]["budget"] == "9890410.98"
    assert "graph_unavailable" in codes(result)


def test_backend_failure_does_not_leak_details(setup):
    service, graph = setup
    graph.get_contract_risk_and_sla.side_effect = RuntimeError("private-backend-details")
    result = service.get_vendor_risk_sla_context("V-001")
    assert "private-backend-details" not in json.dumps(result)
    assert "graph_unavailable" in codes(result)


def test_namespace_mismatch_rejected(setup):
    service, graph = setup
    graph.namespace = "kg-hardening-phase1"
    with pytest.raises(StructuredDataError):
        VendorSemanticService(service.structured, graph)


def test_wrong_response_namespace_excluded(setup):
    service, graph = setup
    graph.get_contract_risk_and_sla.side_effect = None
    graph.get_contract_risk_and_sla.return_value = {"namespace": "kg-hardening-phase1"}
    assert "graph_identity_mismatch" in codes(service.get_vendor_risk_sla_context("V-001"))


def test_unknown_vendor_does_not_call_graph(setup):
    result = setup[0].get_vendor_renewal_context("V-999")
    assert "vendor_not_found" in codes(result)
    assert not setup[1].method_calls


def test_wrong_evidence_namespace_excluded(setup):
    service, graph = setup
    graph.get_evidence_for_entity_or_relationship.return_value["namespace"] = "kg-hardening-phase1"
    result = service.get_vendor_dependency_context("V-001")
    assert "evidence_scope_mismatch" in codes(result)
    assert not any(e["source_type"] == "document" for e in result["evidence"])


def test_evidence_bound_and_truncation(setup):
    service, graph = setup
    graph.get_evidence_for_entity_or_relationship.return_value["citations"]["truncated"] = True
    result = VendorSemanticService(service.structured, graph, max_evidence=1).get_vendor_dependency_context("V-001")
    assert {"evidence_limit", "truncated_evidence"} <= codes(result)
    assert graph.get_evidence_for_entity_or_relationship.call_count == 1


def test_truncated_nonbreaching_sla_is_unknown(setup):
    service, graph = setup
    value = graph.get_contract_risk_and_sla("CTR-001")
    value["measurements"] = collection(value["measurements"]["items"][:1])
    value["measurements"]["truncated"] = True
    graph.get_contract_risk_and_sla.side_effect = None
    graph.get_contract_risk_and_sla.return_value = value
    result = service.get_vendor_risk_sla_context("V-001")
    assert result["facts"]["has_sla_breach"] is None
    assert "truncated_graph" in codes(result)


def test_target_disagreement_remains_unknown(setup):
    service, graph = setup
    value = graph.get_contract_risk_and_sla("CTR-001")
    for p in value["measurements"]["items"]:
        p["nodes"][-1]["properties"]["target"] = 50
    graph.get_contract_risk_and_sla.side_effect = None
    graph.get_contract_risk_and_sla.return_value = value
    result = service.get_vendor_risk_sla_context("V-001")
    assert result["facts"]["has_sla_breach"] is None
    assert "sla_target_mismatch" in codes(result)


def test_portal_is_not_relabelled_as_support(setup):
    result = setup[0].get_vendor_dependency_context("V-017")
    assert {a["relationship"] for a in result["facts"]["applications"]} == {"USES_PORTAL"}


def test_explicit_date_and_grouped_workforce(setup):
    service, _ = setup
    result = service.get_vendor_renewal_context("V-001", as_of_date="2026-09-29")
    assert result["facts"]["days_to_expiry"] == 89
    assert "snapshot_date" in codes(result)
    groups = service.get_vendor_workforce_context("V-001", ["Worker_Type", "Onshore_Offshore"])["facts"]["groups"]
    assert sum(g["representative_workforce_count"] for g in groups) == 4
