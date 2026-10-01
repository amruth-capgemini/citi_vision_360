from pathlib import Path
import json

import pytest

from agent_fakes import DEMOS, ScriptedModel, call, route
from test_semantic_service import canonical_graph
from citi_project.services.agents import ConversationState, SupervisorAgent
from citi_project.services.agents import prompts
from citi_project.services.agents.contracts import ROUTE_SCHEMA, SPECIALISTS
from citi_project.services.agents.openai_model import OpenAIModelConfig
from citi_project.services.decision_intelligence import DecisionIntelligenceService
from citi_project.services.semantic_service import VendorSemanticService
from citi_project.services.structured_data import StructuredQueryService

DATA = Path(__file__).resolve().parents[2] / "initial_plan"


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
        assert "294,920.56" in result["final_answer"]
        assert "no numerical cause is inferred" in result["final_answer"]
    if index == 7:
        assert "399,452.04" in result["final_answer"]
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


def test_vendor_scoped_rationalization_keeps_only_that_vendors_overlaps(decision):
    portfolio = decision.get_vendor_rationalization_opportunities()
    assert "vendor_id" not in portfolio["scope"]
    routing = route(["rationalization"], "V-001", "rationalization")
    calls = [call("get_vendor_rationalization_opportunities", vendor_id="V-001")]
    result = SupervisorAgent(decision, ScriptedModel(routing, calls)).ask("Can we simplify the vendor footprint for V-001?")
    assert result["status"] == "answered" and result["specialists_used"] == ["rationalization"]
    facts = result["tool_results"][0]["result"]["facts"]
    expected = [p for p in portfolio["facts"]["candidates"] if "V-001" in (p["vendor_a"], p["vendor_b"])]
    assert expected and facts["candidates"] == expected and facts["candidate_count"] == len(expected)
    assert set(facts["vendors"]) == {"V-001"} | {v for p in expected for v in (p["vendor_a"], p["vendor_b"])}
    # A vendor question still may not silently run the unscoped portfolio tool.
    loose = [call("get_vendor_rationalization_opportunities")]
    assert SupervisorAgent(decision, ScriptedModel(routing, loose)).ask("Can we simplify the vendor footprint for V-001?")["status"] == "clarification"


def test_route_status_is_decided_last():
    assert ROUTE_SCHEMA["required"][-1] == "status"
    assert list(ROUTE_SCHEMA["properties"])[-1] == "status"


def test_supervisor_prompt_defines_route_and_examples():
    assert prompts.PROMPT_VERSION == "decision-agents-v6"
    assert "What contracts are at major risk?" in prompts.SUPERVISOR
    assert "copy every number, amount, percentage, date and ID exactly" in prompts.SYNTHESIS
    assert "never\nreturn clarification for a missing vendor" in prompts.SPECIALIST
    assert "Default to status route" in prompts.SUPERVISOR
    for question in ("Why is V-005 above budget?", "What if India contractors are reduced by 20%?",
                     "What do we know about Aurelix Codeworks?"):
        assert question.split("?")[0] in prompts.SUPERVISOR
    for name in SPECIALISTS:
        assert f"- {name}:" in prompts.SUPERVISOR
    assert "yourself" in prompts.BOUNDARY


def test_validated_route_recorded_when_answered(decision):
    question, routing, calls = DEMOS[6]
    result = SupervisorAgent(decision, ScriptedModel(routing, calls)).ask(question)
    assert result["status"] == "answered"
    assert result["route"] == routing


@pytest.mark.parametrize("status", ["clarification", "unsupported"])
def test_validated_route_recorded_when_not_actionable(decision, status):
    routing = {**route(["spend_forecast"], "V-005", "spend"), "status": status}
    result = SupervisorAgent(decision, ScriptedModel(routing, [])).ask("Why is V-005 above budget?")
    assert result["status"] == status
    assert result["route"] == routing
    assert result["intent"] == "spend"
    assert {n["code"] for n in result["limitations"]} == {"route_not_actionable"}
    assert not result["tool_results"]


def test_route_absent_before_model_call(decision):
    assert SupervisorAgent(decision, ScriptedModel(None, [])).ask("Delete V-001")["route"] is None


def test_real_two_turn_followup_with_isolated_state(decision):
    model = ScriptedModel(route(["vendor360"], "V-005"), [call("get_vendor_360", vendor_id="V-005")])
    supervisor = SupervisorAgent(decision, model)
    state = ConversationState()
    assert supervisor.ask("What do we know about V-005?", state=state)["status"] == "answered"
    assert state.active_vendor_id == "V-005"
    model.route_result = route(["vendor360"], focus="workforce", active=True)
    assert supervisor.ask("What about its workforce?", state=state)["resolved_entities"]["vendor_ids"] == ["V-005"]
    assert supervisor.ask("What about its workforce?", state=ConversationState())["status"] == "clarification"


def test_followup_router_echoing_the_active_vendor_is_the_followup(decision):
    # Live routers copy the session's vendor into entity_mentions for "this vendor".
    routing = route(["rationalization"], "V-009", "rationalization", active=True)
    calls = [call("get_vendor_rationalization_opportunities", vendor_id="V-009")]
    question = "Where can we simplify the footprint for this vendor?"
    result = SupervisorAgent(decision, ScriptedModel(routing, calls)).ask(question, state=ConversationState("V-009"))
    assert result["status"] == "answered" and result["resolved_entities"]["vendor_ids"] == ["V-009"]
    # An unmentioned vendor that is not the active one is still rejected.
    other = route(["vendor360"], "V-001", active=True)
    result = SupervisorAgent(decision, ScriptedModel(other, [call("get_vendor_360", vendor_id="V-001")])).ask(
        "What do we know about this vendor?", state=ConversationState("V-009"))
    assert result["status"] == "clarification" and not result["tool_results"]


def test_which_vendor_are_we_discussing_is_answered_from_the_session(decision):
    declined = {**route(["vendor360"]), "status": "clarification"}
    state = ConversationState("V-009")
    result = SupervisorAgent(decision, ScriptedModel(declined, [])).ask("which vendor are talking aobut here?", state=state)
    assert result["status"] == "answered" and result["final_answer"].startswith("We are discussing V-009 (Veylora Application Services)")
    assert state.active_vendor_id == "V-009" and not result["tool_results"]
    assert not any(n["code"] == "session_context" for n in result["limitations"])
    result = SupervisorAgent(decision, ScriptedModel(declined, [])).ask("Which vendor are we talking about?")
    assert result["status"] == "clarification" and result["final_answer"].startswith("No vendor is in context yet")
    # A router that routes the question as a follow-up, with no specialist able to plan, still gets the session answer.
    routed = route(["vendor360"], focus="overview", active=True)
    result = SupervisorAgent(decision, ScriptedModel(routed, [])).ask("Which contract are we talking about?", state=state)
    assert result["status"] == "answered" and "V-009" in result["final_answer"] and "CTR-009" not in result["final_answer"]
    # Only a question with no business answer falls back to the session; a business question never does.
    unrelated = SupervisorAgent(decision, ScriptedModel(declined, [])).ask("Please do the thing", state=ConversationState("V-009"))
    assert unrelated["status"] == "clarification" and unrelated["final_answer"].startswith("Please specify")


def test_session_memory_resolves_contracts_and_follow_ups(decision):
    model = ScriptedModel(route(["renewal"], focus="renewal"), [call("get_renewal_context", vendor_id="V-005")])
    supervisor, state = SupervisorAgent(decision, model), ConversationState()
    # A contract ID resolves to its vendor through the canonical master; the model never maps it.
    first = supervisor.ask("Is it worth renewing contract CTR-005?", state=state)
    assert first["status"] == "answered" and first["resolved_entities"]["vendor_ids"] == ["V-005"]
    assert first["resolved_entities"]["contract_ids"] == ["CTR-005"]
    assert (state.active_vendor_id, state.active_contract_id, len(state.history)) == ("V-005", "CTR-005", 1)
    # 'this contract' is the contract in focus, even when the router does not flag the follow-up.
    model.route_result, model.calls = route(["vendor360"], focus="workforce"), [call("get_vendor_360", vendor_id="V-005")]
    follow = supervisor.ask("What are the resources working on this contract?", state=state)
    assert follow["resolved_entities"]["vendor_ids"] == ["V-005"] and follow["resolved_entities"]["contract_ids"] == ["CTR-005"]
    sent = [p for stage, p, _ in model.requests if stage == "supervisor_route"][-1]["conversation"]
    assert sent["active_contract_id"] == "CTR-005" and sent["recent_turns"][0]["question"] == "Is it worth renewing contract CTR-005?"
    # A portfolio question keeps the focus; 'their' in a portfolio question is not a follow-up.
    model.route_result, model.calls = route(["renewal"], focus="renewal"), [call("get_renewal_priorities", days=90)]
    assert supervisor.ask("Which contracts expire in the next 90 days?", state=state)["resolved_entities"]["vendor_ids"] == []
    model.route_result, model.calls = route(["risk_dependency"], focus="risk"), []
    assert supervisor.ask("Which vendors breached their SLAs?", state=state)["resolved_entities"]["vendor_ids"] == []
    assert (state.active_vendor_id, state.active_contract_id) == ("V-005", "CTR-005")
    model.route_result = {**route(["vendor360"]), "status": "clarification"}
    context = supervisor.ask("Which contract are we talking about?", state=state)
    assert context["status"] == "answered" and "V-005" in context["final_answer"] and "CTR-005" in context["final_answer"]
    # An unknown contract is not_found and clears the focus; memory stays bounded.
    assert supervisor.ask("What about CTR-999?", state=state)["status"] == "not_found"
    assert (state.active_vendor_id, state.active_contract_id) == (None, None)
    for _ in range(10):
        supervisor.ask("What about CTR-999?", state=state)
    assert len(state.history) == 6


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
