"""Offline Ask demos and contracts; no database or network access."""

from dataclasses import asdict
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from citi_project.services.knowledge_graph.ask import KnowledgeGraphAskService, route_question
from citi_project.services.knowledge_graph import EntityRef, GraphMapper, GraphPayload
from citi_project.services.knowledge_graph.identity import canonical_identity, relationship_identity
from citi_project.services.knowledge_graph.query_service import KnowledgeGraphQueryService, _entity
from citi_project.services.ontology import OntologyRegistry


DEMOS = [
    ("Which contracts belong to vendor V-001?", "vendor_contracts", "get_vendor_contracts", {"vendor_id": "V-001"}),
    ("Which contracts belong to vendor V-002?", "vendor_contracts", "get_vendor_contracts", {"vendor_id": "V-002"}),
    ("What services and applications are linked to CTR-001?", "contract_dependencies", "get_contract_dependencies", {"contract_id": "CTR-001"}),
    ("What dependencies does CTR-002 have?", "contract_dependencies", "get_contract_dependencies", {"contract_id": "CTR-002"}),
    ("List all clauses for CTR-001.", "contract_clauses", "get_contract_clauses", {"contract_id": "CTR-001"}),
    ("List all clauses for CTR-002.", "contract_clauses", "get_contract_clauses", {"contract_id": "CTR-002"}),
    ("What is the renewal notice for clause renew1 in CTR-001?", "contract_clauses", "get_contract_clauses", {"contract_id": "CTR-001"}),
    ("What SLA performance and risk findings are recorded for CTR-001?", "contract_risk_sla", "get_contract_risk_and_sla", {"contract_id": "CTR-001"}),
    ("Show evidence for RenewalClause renew1 in CTR-001.", "evidence", "get_evidence_for_entity_or_relationship", {"entity": EntityRef("RenewalClause", contract_identity="CTR-001", occurrence_id="renew1")}),
    ("Show evidence for PARTY_TO from Vendor V-001 to Contract CTR-001.", "evidence", "get_evidence_for_entity_or_relationship", {"relationship_type": "PARTY_TO", "source": EntityRef("Vendor", "V-001"), "target": EntityRef("Contract", "CTR-001")}),
]


def fixture_service():
    registry = OntologyRegistry.load()
    payload = GraphPayload.from_dict(json.loads((Path(__file__).parent / "fixtures/knowledge_graph/representative-update.json").read_text(encoding="utf-8")))
    mapped = GraphMapper(registry).map(payload)
    nodes = {n.key: n for n in mapped.nodes}
    edges = {e.key: e for e in mapped.edges}
    q = Mock(spec=KnowledgeGraphQueryService)
    q.namespace, q.registry = payload.namespace, registry

    def paths(start, steps):
        frontier = [([start], [])]
        for types, direction in steps:
            following = []
            for ns, es in frontier:
                for edge in mapped.edges:
                    near, far = (edge.source, edge.target) if direction == "out" else (edge.target, edge.source)
                    if near == ns[-1] and edge.type in types.split("|"):
                        following.append((ns + [far], es + [edge]))
            frontier = following
        return [{"nodes": [_entity(nodes[k].properties) for k in ns], "relationships": [
            {"key": e.key, "type": e.type, "source": e.source, "target": e.target,
             "revision": e.properties["_kg_revision"], "properties": _entity(e.properties)["properties"]}
            for e in es]} for ns, es in frontier]

    funded = [("HAS_SOW", "out"), ("FUNDS", "out")]
    application = funded + [("SUPPORTS|USES_PORTAL", "out")]
    vendor = [("PARTY_TO", "in")]
    dependencies = {"statements_of_work": funded[:1], "services": funded, "applications": application,
                    "configuration_items": funded + [("DEPENDS_ON", "out")],
                    "business_processes": funded + [("SUPPORTS_PROCESS", "out")],
                    "products": application + [("ENABLES", "out")]}
    risk = {"slas": funded + [("HAS_SLA", "out")],
            "measurements": funded + [("HAS_SLA", "out"), ("MEASURED_AGAINST", "in")]}
    for label, prefix in (("vendor", vendor), ("service", funded)):
        risk[label + "_assessments"] = prefix + [("ASSESSES", "in")]
        risk[label + "_assessment_issues"] = prefix + [("ASSESSES", "in"), ("RAISED_IN", "in")]
    for label, prefix in (("vendor", vendor), ("service", funded), ("application", application)):
        risk[label + "_affected_issues"] = prefix + [("AFFECTS", "in")]

    def business(class_id, identity, collections):
        key = canonical_identity(registry, q.namespace, EntityRef(class_id, identity))
        return {"namespace": q.namespace, "limit": 100, "root": _entity(nodes[key].properties) if key in nodes else None,
                **{name: {"items": paths(key, steps) if key in nodes else [], "truncated": False} for name, steps in collections.items()}}

    q.get_vendor_contracts.side_effect = lambda vendor_id: business("Vendor", vendor_id, {"contracts": [("PARTY_TO", "out")]})
    q.get_contract_dependencies.side_effect = lambda contract_id: business("Contract", contract_id, dependencies)
    q.get_contract_clauses.side_effect = lambda contract_id: business("Contract", contract_id, {"clauses": [("HAS_CLAUSE", "out")]})
    q.get_contract_risk_and_sla.side_effect = lambda contract_id: business("Contract", contract_id, risk)

    def evidence(*, entity=None, relationship_type=None, source=None, target=None):
        key = canonical_identity(registry, q.namespace, entity) if entity else relationship_identity(q.namespace, relationship_type, canonical_identity(registry, q.namespace, source), canonical_identity(registry, q.namespace, target))
        row = (nodes if entity else edges).get(key)
        citations = json.loads(row.properties["_kg_provenance"]) if row else []
        doc_keys = {canonical_identity(registry, q.namespace, EntityRef(**c["document"])) for c in citations if c.get("document")}
        return {"found": row is not None, "citations": {"items": citations, "truncated": False},
                "documents": {"items": [_entity(nodes[k].properties) for k in sorted(doc_keys)], "truncated": False},
                "evidenced_by": {"items": paths(key, [("EVIDENCED_BY", "out")]) if entity and row else [], "truncated": False}}
    q.get_evidence_for_entity_or_relationship.side_effect = evidence
    return q


@pytest.mark.parametrize("question,intent,method,arguments", DEMOS)
def test_demos_select_exact_method(question, intent, method, arguments):
    q = fixture_service()
    result = KnowledgeGraphAskService(q).ask(question)
    assert result.status == "answered" and result.intent == intent
    getattr(q, method).assert_called_once_with(**arguments)
    for other in ("get_vendor_contracts", "get_contract_dependencies", "get_contract_clauses", "get_contract_risk_and_sla"):
        if other != method:
            getattr(q, other).assert_not_called()
    assert result.evidence
    assert {"synthetic_provenance", "unverified_provenance"} <= {v["code"] for v in result.limitations}
    json.dumps(result.to_dict())


@pytest.mark.parametrize("question,status", [
    ("", "needs_clarification"), (None, "needs_clarification"),
    ("List vendor contracts", "needs_clarification"),
    ("List clauses for CTR-001 and CTR-002", "needs_clarification"),
    ("List clauses and dependencies for CTR-001", "needs_clarification"),
    ("List clauses for CTR-001x", "needs_clarification"),
    ("List clauses for XCTR-001", "needs_clarification"),
    ("List clauses for CTR-001-2", "needs_clarification"),
    ("List clauses for CTR-01", "needs_clarification"),
    ("List clauses for V-001 and CTR-001", "needs_clarification"),
    ("Forecast next year's spend", "unsupported"),
    ("x" * 1001, "unsupported"),
    ("Show evidence for RenewalClause renew1", "needs_clarification"),
    ("Show evidence for PARTY_TO from Contract CTR-001 to Vendor V-001", "needs_clarification"),
    ("Show evidence for UNKNOWN from Vendor V-001 to Contract CTR-001", "needs_clarification"),
    ("Show evidence for Vendor V-9999", "needs_clarification"),
])
def test_rejected_questions_do_not_call_graph(question, status):
    q = fixture_service()
    assert KnowledgeGraphAskService(q).ask(question).status == status
    assert q.method_calls == []


def test_case_punctuation_and_explicit_evidence():
    assert route_question("  WHICH contracts belong to vendor v-001?! ").arguments == {"vendor_id": "V-001"}
    result = KnowledgeGraphAskService(fixture_service()).ask("Show evidence for contract ctr-001.")
    assert result.status == "answered" and result.evidence[0]["found"]


def test_all_occurrences_and_revision():
    ask = KnowledgeGraphAskService(fixture_service())
    one, two = ask.ask(DEMOS[4][0]), ask.ask(DEMOS[5][0])
    assert len(one.facts_used) == 4 and len(two.facts_used) == 3
    result = ask.ask(DEMOS[6][0])
    assert len(result.facts_used) == 1
    assert "notice_days=45" in result.answer and "revision 2" in result.answer
    assert "renew-support-extension" in one.answer
    renewals = ask.ask("What are the renewal clauses for CTR-001?")
    assert len(renewals.facts_used) == 2


def test_portal_risk_and_distinct_paths():
    ask = KnowledgeGraphAskService(fixture_service())
    result = ask.ask(DEMOS[3][0])
    assert "USES_PORTAL" in result.answer
    assert "-SUPPORTS->" not in result.answer
    products = [f for f in ask.ask(DEMOS[2][0]).facts_used if f["collection"] == "products"]
    assert len(products) == 2
    result = ask.ask("What risk findings exist for CTR-002?")
    assert "USES_PORTAL" in result.answer and "<-AFFECTS- ISSUE-001" in result.answer
    assert "risk tier not recorded" in result.answer


def test_breach_focus_and_missing_facts():
    ask = KnowledgeGraphAskService(fixture_service())
    assert "actual 99.0%" in ask.ask("What SLA breaches exist for CTR-001?").answer
    assert "No matching facts" in ask.ask("What SLA breaches exist for CTR-002?").answer
    assert "not found" in ask.ask("List clauses for CTR-999").answer
    assert "No matching facts" in ask.ask("What is the renewal notice for clause absent in CTR-001?").answer


def test_evidence_matches_subjects_and_is_deduplicated():
    q = fixture_service()
    result = KnowledgeGraphAskService(q).ask(DEMOS[4][0])
    calls = q.get_evidence_for_entity_or_relationship.call_args_list
    assert len(calls) == len({repr(c) for c in calls})
    by_id = {e["evidence_id"]: e for e in result.evidence}
    for fact in result.facts_used:
        clause = fact["path"]["nodes"][-1]
        selector = {"entity": asdict(EntityRef(clause["class_id"], contract_identity="CTR-001", occurrence_id=clause["occurrence_id"]))}
        assert any(by_id[e]["subject"] == selector for e in fact["evidence_ids"])
    assert all(e["citations"] for e in result.evidence)


def test_limits_truncation_and_missing_provenance():
    q = fixture_service()
    result = KnowledgeGraphAskService(q, max_facts=1, max_evidence=1).ask(DEMOS[4][0])
    assert len(result.facts_used) == len(result.evidence) == 1
    assert {"fact_limit", "evidence_limit"} <= {v["code"] for v in result.limitations}
    original = q.get_contract_clauses.side_effect
    def truncated(contract_id):
        value = original(contract_id)
        value["clauses"]["truncated"] = True
        return value
    q.get_contract_clauses.side_effect = truncated
    q.get_evidence_for_entity_or_relationship.side_effect = lambda **kw: {
        "found": True, "citations": {"items": [], "truncated": True},
        "documents": {"items": [], "truncated": False}, "evidenced_by": {"items": [], "truncated": False}}
    result = KnowledgeGraphAskService(q).ask(DEMOS[4][0])
    assert {"truncated", "missing_provenance"} <= {v["code"] for v in result.limitations}


def test_unresolved_document_and_missing_evidence_entity():
    q = fixture_service()
    original = q.get_evidence_for_entity_or_relationship.side_effect
    def unresolved(**kw):
        value = original(**kw)
        value["documents"]["items"] = []
        return value
    q.get_evidence_for_entity_or_relationship.side_effect = unresolved
    result = KnowledgeGraphAskService(q).ask(DEMOS[8][0])
    assert "unresolved_document" in {v["code"] for v in result.limitations}
    assert "not found" in KnowledgeGraphAskService(q).ask("Show evidence for Vendor V-999").answer


@pytest.mark.parametrize("where", ["get_contract_clauses", "get_evidence_for_entity_or_relationship"])
def test_errors_are_sanitized(where):
    q = fixture_service()
    getattr(q, where).side_effect = RuntimeError("sensitive-backend-detail")
    result = KnowledgeGraphAskService(q).ask(DEMOS[4][0])
    assert result.status == "error" and not result.facts_used and not result.evidence
    assert "sensitive-backend-detail" not in json.dumps(result.to_dict())


@pytest.mark.parametrize("kwargs", [{"max_facts": 0}, {"max_facts": True}, {"max_evidence": 201}])
def test_invalid_bounds(kwargs):
    with pytest.raises(ValueError):
        KnowledgeGraphAskService(fixture_service(), **kwargs)


def test_only_existing_read_methods_are_available():
    q = fixture_service()
    for question, *_ in DEMOS:
        KnowledgeGraphAskService(q).ask(question)
    assert all(call[0] in {demo[2] for demo in DEMOS} for call in q.method_calls)


if __name__ == "__main__":
    ask = KnowledgeGraphAskService(fixture_service())
    for question, *_ in DEMOS:
        result = ask.ask(question)
        print(json.dumps({"question": question, "status": result.status, "answer": result.answer,
                          "facts": len(result.facts_used), "evidence": len(result.evidence),
                          "limitations": [v["code"] for v in result.limitations]}))
