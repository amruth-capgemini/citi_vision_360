from copy import deepcopy
from unittest.mock import Mock

import pytest

from agent_fakes import DEMOS, ScriptedModel, call, narrative, route
from test_agent_routing import decision
from citi_project.services.agents import AgentError, ConversationState, ModelError, SupervisorAgent
from citi_project.services.agents.tools import ApprovedTools, CanonicalResolver, EntityResolutionError


@pytest.mark.parametrize("mode,category", [
    ("context", None), ("decline", "all_specialists_declined"),
    ("portfolio", "vendor_scope_missing"), ("wrong_vendor", "vendor_not_resolved"),
    ("canonical_id", None), ("unresolved_id", "entity_not_mentioned"),
    ("invented_id", "entity_not_mentioned"), ("unmentioned_name", "entity_not_mentioned"),
    ("invented_date", "invented_scope"),
])
def test_aurelix_renewal_diagnostics(decision, caplog, mode, category):
    import logging

    question = "Should we renew Aurelix Codeworks?"
    resolver = CanonicalResolver(decision.structured)
    assert resolver.explicit(question) == ["V-001"]
    assert resolver.resolve("V-002") == "V-002"
    assert "V-999" not in {v["vendor_id"] for v in resolver.catalog}
    routing = route(["renewal"], "Aurelix Codeworks", "renewal")
    calls = [call("get_renewal_context", vendor_id="V-001")]
    if mode == "decline":
        calls = []
    elif mode == "portfolio":
        calls = [call("get_renewal_priorities", days=90)]
    elif mode == "wrong_vendor":
        calls[0]["arguments"]["vendor_id"] = "V-002"
    elif mode == "canonical_id":
        routing["entity_mentions"] = ["V-001"]
    elif mode == "unresolved_id":
        routing["entity_mentions"] = ["V-002"]
    elif mode == "invented_id":
        routing["entity_mentions"] = ["V-999"]
    elif mode == "unmentioned_name":
        routing["entity_mentions"] = ["Velmora Cloud Operations"]
    elif mode == "invented_date":
        calls[0]["arguments"]["as_of_date"] = "2030-01-01"
    with caplog.at_level(logging.INFO, logger="citi_project.services.agents"):
        result = SupervisorAgent(decision, ScriptedModel(routing, calls)).ask(question)
    assert result["status"] == ("answered" if category is None else "clarification")
    assert result["route"] == routing
    assert "specialists=['renewal']" in caplog.text
    assert "status=resolved vendor_id=V-001" in caplog.text
    if category:
        assert f"category={category}" in caplog.text
        assert not result["tool_results"]
    else:
        assert result["resolved_entities"]["vendor_ids"] == ["V-001"]
        assert "tool=get_renewal_context" in caplog.text
        assert "parameter_keys=['as_of_date', 'days', 'vendor_id']" in caplog.text
    assert question not in caplog.text
    assert "Aurelix Codeworks" not in caplog.text
    assert "2030-01-01" not in caplog.text


def test_diagnostic_exception_payload_is_not_logged(caplog):
    from citi_project.services.agents.graph import _failure

    _failure(AgentError("private-secret-placeholder"))
    assert "category=other_boundary_failure" in caplog.text
    assert "private-secret-placeholder" not in caplog.text


@pytest.mark.parametrize("question", ["Delete V-001", "Update the Neo4j data", "Execute Python", "Run Cypher against the graph", "Commit and push", "Overwrite the CSVs"])
def test_write_and_code_requests_rejected_without_llm(decision, question):
    model = Mock()
    result = SupervisorAgent(decision, model).ask(question)
    assert result["status"] == "unsupported"
    model.complete.assert_not_called()


@pytest.mark.parametrize("mention,status", [("Services", "clarification"), ("Aurelix", "clarification"), ("Unknown Vendor", "not_found"), ("V-999", "not_found")])
def test_no_silent_fuzzy_resolution(decision, mention, status):
    with pytest.raises(EntityResolutionError) as exc:
        CanonicalResolver(decision.structured).resolve(mention)
    assert exc.value.status == status


def test_invalid_explicit_vendor_does_not_use_old_state(decision):
    state = ConversationState("V-001")
    result = SupervisorAgent(decision, Mock()).ask("What about V-999?", state=state)
    assert result["status"] == "not_found"
    assert state.active_vendor_id is None


def test_model_cannot_guess_id_for_a_name(decision):
    model = ScriptedModel(route(["vendor360"], "V-002"), [call("get_vendor_360", vendor_id="V-002")])
    result = SupervisorAgent(decision, model).ask("What do we know about Aurelix Codeworks?")
    assert result["status"] == "clarification"
    assert not result["tool_results"]


def test_wrong_specialist_permission(decision):
    tools = ApprovedTools(decision)
    with pytest.raises(AgentError):
        tools.prepare("vendor360", call("get_spend_forecast_analysis", vendor_id="V-001"), "V-001", ["V-001"])


@pytest.mark.parametrize("change", [
    {"vendor_id": "V-002"}, {"as_of_date": "2030-01-01"}, {"namespace": "kg-hardening-phase1"},
    {"python": "print('x')"},
])
def test_unapproved_scope_and_arguments_fail_closed(decision, change):
    c = call("get_vendor_360", vendor_id="V-001")
    c["arguments"].update(change)
    result = SupervisorAgent(decision, ScriptedModel(route(["vendor360"], "V-001"), [c])).ask("What about V-001?")
    assert result["status"] == "clarification"
    assert not result["tool_results"]


def test_full_plan_checked_before_tools_run(decision):
    q, r, calls = deepcopy(DEMOS[11])
    calls[-1]["arguments"]["vendor_id"] = "V-001"
    result = SupervisorAgent(decision, ScriptedModel(r, calls)).ask(q)
    assert result["status"] == "clarification"
    assert result["tool_results"] == []


def test_bounded_calls(decision):
    q, r, calls = DEMOS[11]
    result = SupervisorAgent(decision, ScriptedModel(r, calls), max_tool_calls=2).ask(q)
    assert result["status"] == "clarification"
    assert not result["tool_results"]


def test_explicit_organization_cannot_be_omitted(decision):
    model = ScriptedModel(route(["rationalization"], focus="rationalization"), [call("get_vendor_rationalization_opportunities")])
    result = SupervisorAgent(decision, model).ask("Find candidates in ORG-01")
    assert result["status"] == "clarification"
    assert not result["tool_results"]


def test_multi_vendor_cannot_silently_omit_one(decision):
    model = ScriptedModel(route(["vendor360"]), [call("get_vendor_360", vendor_id="V-001")])
    result = SupervisorAgent(decision, model).ask("Compare V-001 and V-005")
    assert result["status"] == "clarification"
    assert not result["tool_results"]


def test_duplicate_calls_rejected(decision):
    q, r, calls = DEMOS[0]
    result = SupervisorAgent(decision, ScriptedModel(r, calls * 2)).ask(q)
    assert result["status"] == "clarification"


@pytest.mark.parametrize("synthesis", [
    {"selected_fact_ids": ["F1"]},                                            # wrong shape
    narrative("F999"),                                                        # unknown card
    narrative("F1", text="Cutting this vendor would save USD 1,000,000."),    # invented amount
    narrative("F2", text="The variance is USD 294,920.56."),                  # real value, but not in the cited card
    narrative("F1", text="The contract with V-777 runs to 2027-03-01."),      # invented ID and date
    narrative("F1", text="You should terminate this contract."),              # a recommendation
])
def test_unverified_answer_falls_back_to_source_text(decision, synthesis):
    q, r, calls = DEMOS[6]
    model = ScriptedModel(r, calls, synthesis=synthesis)
    result = SupervisorAgent(decision, model).ask(q)
    assert result["status"] == "answered"
    assert "synthesis_fallback" in {x["code"] for x in result["limitations"]}
    for text in ("1,000,000", "V-777", "terminate this contract"):
        assert text not in result["final_answer"]
    assert "294,920.56" in result["final_answer"] and result["final_answer"].startswith("Key facts:")
    # A schema-valid answer that fails the value checks gets exactly one retry, told what failed.
    stages = [(stage, payload) for stage, payload, _ in model.requests if stage == "supervisor_synthesis"]
    if "paragraphs" in synthesis and synthesis["paragraphs"][0]["fact_ids"] != ["F999"]:
        assert len(stages) == 2 and stages[1][1]["previous_problems"]


def test_verified_answer_is_written_in_prose_with_citations(decision):
    q, r, calls = DEMOS[6]
    written = {"summary": "V-005 is forecast to exceed its 2026 budget by USD 294,920.56, or 11.41%.",
               "paragraphs": [{"heading": "Full-year outlook", "fact_ids": ["F1"],
                               "text": "The 2026 budget is USD 2,585,753.42 and the forecast is USD 2,880,673.98, "
                                       "so the forecast runs USD 294,920.56 (11.41%) above budget [F1]."}]}
    result = SupervisorAgent(decision, ScriptedModel(r, calls, synthesis=written)).ask(q)
    assert "synthesis_fallback" not in {x["code"] for x in result["limitations"]}
    answer = result["final_answer"]
    assert answer.startswith("V-005 is forecast to exceed its 2026 budget by USD 294,920.56, or 11.41%.")
    assert "Full-year outlook\nThe 2026 budget is USD 2,585,753.42" in answer and "above budget. [F1]" in answer
    assert "\nNotes:\n- Decision context only" in answer and "no numerical cause is inferred" in answer
    assert answer.count("[F1]") == 1  # the host cites once, even if the model wrote its own citation
    assert result["narrative"] == written and result["synthesis_checks"] == {"attempts": 1, "problems": []}


def test_model_cannot_omit_deterministic_limitations(decision):
    q, r, calls = DEMOS[3]
    result = SupervisorAgent(decision, ScriptedModel(r, calls, synthesis=narrative("F1"))).ask(q)
    assert "synthesis_fallback" not in {x["code"] for x in result["limitations"]}
    assert "Risk assessment evidence is unavailable" in result["final_answer"]
    assert any(n["code"] == "risk_missing" for n in result["limitations"])


def test_model_cannot_omit_returned_expiring_contracts(decision):
    q, r, calls = DEMOS[1]
    result = SupervisorAgent(decision, ScriptedModel(r, calls, synthesis=narrative("F1"))).ask(q)
    assert "Key facts:" in result["final_answer"]
    assert all(f"{vendor}: contract expires" in result["final_answer"] for vendor in ("V-005", "V-018", "V-009", "V-007", "V-013", "V-001"))


def test_selected_cards_cover_all_specialists(decision):
    q, r, calls = DEMOS[11]
    result = SupervisorAgent(decision, ScriptedModel(r, calls, synthesis=narrative("F1"))).ask(q)
    assert {c["tool_id"] for c in result["facts"] if c["fact_id"] in result["selected_fact_ids"]} == {"T1", "T2", "T3"}


def test_scenario_count_bounded_and_baseline_immutable(decision):
    q, r, calls = deepcopy(DEMOS[10])
    before = decision.run_workforce_scenario(country="India")
    result = SupervisorAgent(decision, ScriptedModel(r, calls)).ask(q)
    assert result["status"] == "answered"
    assert decision.run_workforce_scenario(country="India") == before
    calls[0]["arguments"]["assignment_count"] = 10
    result = SupervisorAgent(decision, ScriptedModel(r, calls)).ask(q.replace("two", "ten"))
    assert result["status"] == "clarification"


@pytest.mark.parametrize("change", [{"percentage": 50}, {"country": "United States"}, {"assignment_ids": ["ASN-001-002"]}])
def test_scenario_model_cannot_invent_parameters(decision, change):
    q, r, calls = deepcopy(DEMOS[9])
    calls[0]["arguments"].update(change)
    result = SupervisorAgent(decision, ScriptedModel(r, calls)).ask(q)
    assert result["status"] == "clarification"


def test_model_error_sanitized(decision):
    model = Mock()
    model.complete.side_effect = ModelError("private-secret-placeholder")
    result = SupervisorAgent(decision, model).ask("What about V-001?")
    assert result["status"] == "unavailable"
    assert "private-secret-placeholder" not in str(result)


def test_namespace_change_rejected(decision):
    decision.structured.namespace = "kg-hardening-phase1"
    with pytest.raises(AgentError):
        SupervisorAgent(decision, Mock())


def test_empty_expiry_result_is_answerable(decision):
    model = ScriptedModel(route(["renewal"], focus="renewal"), [call("get_renewal_priorities", days=0)])
    result = SupervisorAgent(decision, model).ask("Which contracts expire in 0 days?")
    assert result["status"] == "answered"
    assert "No matching facts" in result["final_answer"]
