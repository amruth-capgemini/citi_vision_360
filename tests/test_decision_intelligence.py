"""Deterministic decisions-context tests using canonical CSVs and graph doubles."""

from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest

from citi_project.services.decision_intelligence import DecisionIntelligenceService, DecisionPolicy
from citi_project.services.semantic_service import VendorSemanticService
from citi_project.services.structured_data import StructuredDataError, StructuredQueryService
from test_semantic_service import canonical_graph, collection, node, path

DATA = Path(__file__).resolve().parents[1] / "initial_plan"


@pytest.fixture
def setup():
    structured = StructuredQueryService(DATA)
    graph = canonical_graph(structured)
    semantic = VendorSemanticService(structured, graph)
    return DecisionIntelligenceService(semantic), graph


def flag_codes(result):
    return {f["code"] for f in result["flags"]}


def test_vendor_360_sections_and_evidence(setup):
    result = setup[0].get_vendor_360("V-001")
    assert result["namespace"] == "synthetic-pack-20260928"
    assert result["capability"] == "vendor_360"
    assert set(result["facts"]) == {"identity", "commercial", "financial", "workforce", "dependencies", "sla", "risk", "evidence_completeness"}
    assert result["facts"]["identity"]["Contract_ID"] == "CTR-001"
    assert result["facts"]["commercial"]["sow_id"] == "SOW-001"
    assert result["facts"]["dependencies"]["application_count"] == 4
    assert result["facts"]["financial"]["forecast"] == "10120547.96"
    assert result["facts"]["workforce"]["representative_workforce_count"] == 4
    assert result["facts"]["sla"]["has_breach"] is True
    assert result["facts"]["evidence_completeness"]["business_verified"] is False
    assert any(e["source_type"] == "document" and e["evidence_ref"] == "synthetic:offline-test-sentinel" for e in result["evidence"])
    assert any(e["source_record_id"] == "SYN-FIN-2026-001-FORECAST" for e in result["evidence"] if e["source_type"] == "structured")
    assert not result["flags"]
    json.dumps(result)


def test_renewal_priorities_expiry_order_and_no_arbitrary_thresholds(setup):
    result = setup[0].get_renewal_priorities()
    assert [c["scope"]["vendor_id"] for c in result["facts"]["contracts"]] == ["V-005", "V-018", "V-009", "V-007", "V-013", "V-001"]
    assert "HIGH_FORECAST_VARIANCE" not in flag_codes(result)
    assert "HIGH_DEPENDENCY" not in flag_codes(result)
    assert "STALE_RISK" in flag_codes(result)
    assert sum(f["code"] == "EXPIRING_SOON" for f in result["flags"]) == 6


def test_v005_renewal_configurable_flags(setup):
    svc = DecisionIntelligenceService(setup[0].semantic, policy=DecisionPolicy("10", 3))
    result = svc.get_renewal_context("V-005")
    assert result["facts"]["commercial"]["days_to_expiry"] == 15
    assert flag_codes(result) == {"EXPIRING_SOON", "HIGH_FORECAST_VARIANCE", "HIGH_DEPENDENCY"}
    boundary = DecisionIntelligenceService(setup[0].semantic, policy=DecisionPolicy("11.41", 4)).get_renewal_context("V-005")
    assert flag_codes(boundary) == {"EXPIRING_SOON"}


def test_v009_dependency_risk_keeps_stale_tier_historical(setup):
    f = setup[0].get_vendor_dependency_risk("V-009")["facts"]
    assert f["dependencies"]["supported_application_count"] == 4
    assert f["dependencies"]["used_portal_count"] == 0
    assert f["dependencies"]["service_count"] == 1
    assert f["workforce"]["representative_workforce_count"] == 4
    assert f["risk"]["status"] == "Stale"
    assert f["risk"]["assessments"][0]["assessment_date"] == "2025-06-01"
    assert f["sla"]["breached_measurement_count"] == 1


def test_v017_risk_missing_and_portal_distinguished(setup):
    result = setup[0].get_renewal_context("V-017")
    assert "MISSING_RISK" in flag_codes(result)
    assert "EXPIRING_SOON" not in flag_codes(result)
    assert result["facts"]["risk"]["assessments"][0]["risk_tier"] is None
    assert result["facts"]["dependencies"]["used_portal_count"] == 1
    assert result["facts"]["dependencies"]["supported_application_count"] == 0


def test_rationalization_exact_overlap_no_financial_multiplication(setup):
    svc = setup[0]
    result = svc.get_vendor_rationalization_opportunities()
    pairs = result["facts"]["candidates"]
    assert len({(p["vendor_a"], p["vendor_b"]) for p in pairs}) == len(pairs)
    pair = next(p for p in pairs if (p["vendor_a"], p["vendor_b"]) == ("V-001", "V-005"))
    assert pair["overlap"]["product"] is True
    assert pair["overlap"]["organization"] is True
    assert pair["overlap"]["shared_applications"] == ["APP-001", "APP-002"]
    assert pair["overlap"]["shared_services"] == []
    assert pair["financial_context"]["V-001"]["forecast"] == "10120547.96"
    assert sum(Decimal(v["financial"]["forecast"]) for v in result["facts"]["vendors"].values()) == Decimal("55228019.17")
    assert all(p["classification"] == "potential_overlap_candidate" for p in pairs)


def test_org01_rationalization(setup):
    result = setup[0].get_vendor_rationalization_opportunities(organization_id="ORG-01")
    assert set(result["facts"]["vendors"]) == {"V-001", "V-005", "V-009", "V-013", "V-017"}
    assert result["facts"]["candidate_count"] == 10
    assert all(p["overlap"]["organization"] for p in result["facts"]["candidates"])


def test_spend_v005_above_budget_not_above_comparable_forecast(setup):
    result = setup[0].get_spend_forecast_analysis("V-005")
    f = result["facts"]
    assert f["forecast_variance_amount"] == "294920.56"
    assert f["forecast_variance_pct"] == "11.41"
    assert f["actual_vs_forecast_ytd"] == {"amount": "0.00", "percent": "0.00"}
    assert f["allocation"]["BCID"] == "BC20005"
    assert "causal_drivers_unavailable" in {n["code"] for n in result["limitations"]}


def test_spend_v009_comparable_budget(setup):
    f = setup[0].get_spend_forecast_analysis("V-009")["facts"]
    assert f["actual_ytd"] == "3728219.13"
    assert f["comparable_ytd_budget"] == "3328767.09"
    assert f["actual_ytd_variance_amount"] == "399452.04"
    assert f["actual_ytd_variance_pct"] == "12.00"


def test_india_baseline(setup):
    result = setup[0].run_workforce_scenario(country="India")
    assert result["facts"]["baseline"]["representative_assignment_count"] == 60
    assert result["facts"]["selected_baseline"]["representative_assignment_count"] == 13
    assert result["facts"]["affected_assignment_count"] == 0
    assert result["facts"]["financial_impact"] == "unavailable"


def test_india_contractor_reduction_rounding_and_no_mutation(setup):
    svc = setup[0]
    before = svc.structured.get_vendor_workforce("V-001")
    result = svc.run_workforce_scenario(action="reduce", percentage=20, country="India", worker_type="Contractor")
    f = result["facts"]
    assert f["selected_baseline"]["representative_assignment_count"] == 4
    assert f["affected_assignment_ids"] == ["ASN-001-002"]
    assert f["resulting_counts"]["representative_assignment_count"] == 59
    assert f["selected_resulting_counts"]["representative_assignment_count"] == 3
    assert result["calculations"][0]["effective_percentage"] == "25.00"
    assert result["calculations"][0]["unrounded_assignment_equivalent"] == "0.80"
    assert svc.structured.get_vendor_workforce("V-001") == before
    assert result == svc.run_workforce_scenario(action="reduce", percentage=20, country="India", worker_type="Contractor")


def test_selected_category_shift_conserves_total(setup):
    svc = setup[0]
    result = svc.run_workforce_scenario(action="shift", percentage=100, country="India", worker_type="Contractor", target_worker_type="Consultant", assignment_ids=["ASN-004-002", "ASN-001-002"])
    assert result["facts"]["affected_assignment_count"] == 2
    assert result["facts"]["resulting_counts"]["representative_assignment_count"] == 60
    groups = {(r["country"], r["worker_type"]): r["representative_assignment_count"] for r in result["facts"]["resulting_counts"]["groups"]}
    assert groups[("India", "Contractor")] == 2
    assert groups[("India", "Consultant")] == 6
    assert svc.run_workforce_scenario(country="India", worker_type="Contractor")["facts"]["selected_baseline"]["representative_assignment_count"] == 4


def test_country_shift_preserves_identity_and_count(setup):
    result = setup[0].run_workforce_scenario(action="shift", percentage=100, country="India", target_country="United Kingdom")
    assert result["facts"]["affected_assignment_count"] == 13
    assert result["facts"]["resulting_counts"]["representative_assignment_count"] == 60
    assert not any(g["country"] == "India" for g in result["facts"]["resulting_counts"]["groups"])


def test_already_at_target_excluded(setup):
    result = setup[0].run_workforce_scenario(action="shift", percentage=100, country="India", target_country="India")
    assert result["facts"]["eligible_assignment_count"] == 0
    assert result["facts"]["affected_assignment_count"] == 0


def test_scenario_vendor_org_market_and_worker_filters(setup):
    result = setup[0].run_workforce_scenario(vendor_id="V-001", organization_id="ORG-01", market="india", worker_type="contractor")
    assert result["facts"]["baseline"]["representative_assignment_count"] == 4
    assert result["facts"]["selected_baseline"]["representative_assignment_count"] == 1
    empty = setup[0].run_workforce_scenario(vendor_id="V-001", organization_id="ORG-02")
    assert empty["facts"]["selected_baseline"]["representative_assignment_count"] == 0


@pytest.mark.parametrize("params", [
    {"percentage": -1}, {"percentage": 101}, {"percentage": "NaN"}, {"percentage": True},
    {"action": "baseline", "percentage": 20}, {"action": "unsupported"},
    {"action": "shift", "percentage": 20}, {"action": "reduce", "target_country": "India"},
    {"action": "shift", "target_worker_type": "Invented"},
    {"assignment_ids": ["ASN-999-001"]}, {"assignment_ids": ["ASN-001-001", "ASN-001-001"]},
    {"country": 1}, {"organization_id": "wrong"},
])
def test_invalid_scenario_inputs(setup, params):
    with pytest.raises(StructuredDataError):
        setup[0].run_workforce_scenario(**params)


@pytest.mark.parametrize("params", [{"high_forecast_variance_pct": "NaN"}, {"high_forecast_variance_pct": -1}, {"high_dependency_count": True}, {"high_dependency_count": 0}])
def test_invalid_policy(params):
    with pytest.raises(StructuredDataError):
        DecisionPolicy(**params)


def test_policy_serializable():
    from dataclasses import asdict
    json.dumps(asdict(DecisionPolicy(Decimal("10"), 4)))


def test_namespace_rejected(setup):
    svc, graph = setup
    graph.namespace = "kg-hardening-phase1"
    with pytest.raises(StructuredDataError):
        DecisionIntelligenceService(svc.semantic)


def test_absent_graph_does_not_imply_zero_risk():
    svc = DecisionIntelligenceService(VendorSemanticService(StructuredQueryService(DATA)))
    result = svc.get_renewal_context("V-001")
    assert result["facts"]["dependencies"]["application_count"] is None
    assert result["facts"]["sla"]["has_breach"] is None
    assert result["facts"]["sla"]["breached_measurement_count"] is None
    assert result["facts"]["risk"]["open_issue_count"] is None
    assert "INCOMPLETE_EVIDENCE" in flag_codes(result)


def test_open_issues_distinct_across_paths(setup):
    svc, graph = setup
    value = graph.get_contract_risk_and_sla("CTR-001")
    root = value["root"]
    issue = node("RiskIssue", "ISSUE-001", {"status": "Open"})
    p = path([root, issue], ["TEST_ONLY_ISSUE_PATH"])
    value["vendor_assessment_issues"] = collection([p])
    value["service_affected_issues"] = collection([deepcopy(p)])
    graph.get_contract_risk_and_sla.side_effect = None
    graph.get_contract_risk_and_sla.return_value = value
    result = svc.get_vendor_dependency_risk("V-001")
    assert result["facts"]["risk"]["open_issue_count"] == 1


def test_truncated_dependency_counts_are_lower_bounds(setup):
    svc, graph = setup
    value = graph.get_contract_dependencies("CTR-001")
    value["applications"]["truncated"] = True
    graph.get_contract_dependencies.side_effect = None
    graph.get_contract_dependencies.return_value = value
    result = svc.get_vendor_dependency_risk("V-001")
    assert result["facts"]["dependencies"]["complete"] is False
    assert result["facts"]["evidence_completeness"]["retrieval_status"] == "incomplete"


def test_missing_vendor_year_empty_scope_and_bounds(setup):
    svc = setup[0]
    assert svc.get_vendor_360("V-999")["facts"] == {}
    assert svc.get_spend_forecast_analysis("V-001", 2027)["facts"]["forecast_ytd"] is None
    assert svc.get_vendor_rationalization_opportunities(organization_id="ORG-99")["facts"]["candidate_count"] == 0
    assert svc.get_renewal_priorities(0, "2026-10-14")["facts"]["contracts"] == []
    with pytest.raises(StructuredDataError):
        DecisionIntelligenceService(svc.semantic, max_vendors=2).get_vendor_rationalization_opportunities()


def test_master_enumeration_and_detachment(setup):
    structured = setup[0].structured
    result = structured.list_vendor_contracts()
    assert len(result["facts"]["contracts"]) == 20
    result["facts"]["contracts"][0]["Vendor_ID"] = "bad"
    assert structured.list_vendor_contracts()["facts"]["contracts"][0]["Vendor_ID"] == "V-001"
    assert len(structured.list_vendor_contracts("ORG-01")["facts"]["contracts"]) == 5


def test_all_generated_files_unchanged_after_scenario(setup):
    report = json.loads((DATA / "generated/validation_report.json").read_text(encoding="utf-8"))
    setup[0].run_workforce_scenario(action="reduce", percentage=100)
    for name, metadata in report["files"].items():
        assert hashlib.sha256((DATA / name).read_bytes()).hexdigest() == metadata["sha256"]
