from pathlib import Path
import json

import pytest

from agent_fakes import DEMOS, ScriptedModel, call, route
from test_semantic_service import canonical_graph
from citi_project.services.agents import ConversationState, SupervisorAgent
from citi_project.services.agents import prompts
from citi_project.services.agents.openai_model import OpenAIModelConfig
from citi_project.services.decision_intelligence import DecisionIntelligenceService
from citi_project.services.semantic_service import VendorSemanticService
from citi_project.services.structured_data import StructuredQueryService

DATA = Path(__file__).resolve().parents[1] / "initial_plan"


@pytest.fixture
def decision():
    structured = StructuredQueryService(DATA)
    return DecisionIntelligenceService(VendorSemanticService(structured, canonical_graph(structured)))


@pytest.mark.parametrize("index", range(15))
def test_fifteen_scripted_questions(decision, index):
    question, routing, calls = DEMOS[index]
    model = ScriptedModel(routing, calls)
    state = ConversationState("V-005") if index == 13 else ConversationState()
    result = SupervisorAgent(decision, model).ask(question, state=state)
    assert result["status"] == ("clarification" if index == 14 else "answered")
    if index == 14:
        assert len(result["resolved_entities"]["candidates"]) > 1
        assert not result["tool_results"]
        return
    assert result["specialists_used"] == routing["specialists"]
    assert [r["tool"] for r in result["tool_results"]] == [c["name"] for c in calls]
    assert result["evidence"]
    assert result["limitations"]
    assert "synthetic" in result["final_answer"].lower()
    assert all(t["result"]["namespace"] == "synthetic-pack-20260928" for t in result["tool_results"])
    if index == 0:
        assert "4 representative workforce assignments" in result["final_answer"]
        assert "SLA breach yes" in result["final_answer"]
    if index == 3:
        assert "Missing" in result["final_answer"] and "no risk tier inferred" in result["final_answer"]
    if index == 6:
        assert "294920.56" in result["final_answer"]
        assert "no numerical cause is inferred" in result["final_answer"]
    if index == 7:
        assert "399452.04" in result["final_answer"]
    if index == 8:
        assert "13 representative assignments" in result["final_answer"]
    if index == 9:
        assert "25.00%" in result["final_answer"]
    if index == 10:
        tool = result["tool_results"][0]
        assert tool["arguments"]["assignment_ids"] == ["ASN-001-002", "ASN-004-002"]
        assert tool["result"]["facts"]["affected_assignment_count"] == 2
        assert tool["result"]["facts"]["financial_impact"] == "unavailable"
    if index == 12:
        assert result["resolved_entities"]["vendor_ids"] == ["V-001"]
    if index == 13:
        assert "V-005: 3 representative workforce assignments" in result["final_answer"]
    json.dumps(result)
    for stage, payload, schema in model.requests:
        if stage == "supervisor_synthesis":
            assert len(json.dumps(payload)) + len(json.dumps(schema)) + len(prompts.SYNTHESIS) <= OpenAIModelConfig().max_input_chars


def test_real_two_turn_followup_with_isolated_state(decision):
    model = ScriptedModel(route(["vendor360"], "V-005"), [call("get_vendor_360", vendor_id="V-005")])
    supervisor = SupervisorAgent(decision, model)
    state = ConversationState()
    assert supervisor.ask("What do we know about V-005?", state=state)["status"] == "answered"
    assert state.active_vendor_id == "V-005"
    model.route_result = route(["vendor360"], focus="workforce", active=True)
    assert supervisor.ask("What about its workforce?", state=state)["resolved_entities"]["vendor_ids"] == ["V-005"]
    assert supervisor.ask("What about its workforce?", state=ConversationState())["status"] == "clarification"


def test_new_entity_overrides_followup(decision):
    q, r, calls = DEMOS[0]
    state = ConversationState("V-005")
    response = SupervisorAgent(decision, ScriptedModel(r, calls)).ask(q, state=state)
    assert response["resolved_entities"]["vendor_ids"] == ["V-001"]
    assert state.active_vendor_id == "V-001"


def test_renewal_card_evidence_matches_vendor(decision):
    q, routing, calls = DEMOS[1]
    result = SupervisorAgent(decision, ScriptedModel(routing, calls)).ask(q)
    evidence = {e["evidence_id"]: e["source"] for e in result["evidence"]}
    for card in result["facts"]:
        if card["text"].startswith("V-001") and card["topic"] == "spend":
            assert card["evidence_ids"]
            assert all("001" in evidence[e]["source_record_id"] for e in card["evidence_ids"])


def test_evidence_and_source_calculations_preserved(decision):
    q, r, calls = DEMOS[6]
    model = ScriptedModel(r, calls)
    result = SupervisorAgent(decision, model).ask(q)
    original = result["tool_results"][0]["result"]
    assert [c["value"] for c in result["calculations"]] == original["calculations"]
    assert all(item in result["limitations"] for item in original["limitations"])
    assert all(item in [e["source"] for e in result["evidence"]] for item in original["evidence"])
    payload = next(p for stage, p, _ in model.requests if stage == "supervisor_synthesis")
    assert payload["evidence"] and payload["limitations"]
