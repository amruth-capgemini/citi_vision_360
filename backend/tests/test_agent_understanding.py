"""The supervisor understands questions against the data model and explains what it cannot answer.

Replays the chat that motivated it: after 'What do we know about Aurelix Codeworks?', the
follow-ups 'are there any application names?' and 'who is ORG-01' must keep the vendor in
focus, type ORG-01 as an organization (never a vendor), and say why when data is missing.
"""

from agent_fakes import ScriptedModel, call, route
from explore_fakes import ExplorerModel, FixedExecutor, step
from test_agent_routing import decision
from test_explorer import explorer
from citi_project.services.agents import ConversationState, SupervisorAgent
from citi_project.services.agents.digest import DataDigest

APPS = "MATCH (a:Application {_kg_namespace: $namespace}) WHERE a.application_id IN ['APP-001', 'APP-002'] RETURN a.application_id, a.name"


def focused():
    return ConversationState("V-001", "CTR-001")


def codes(result):
    return {n["code"] for n in result["limitations"]}


# ------------------------------------------------------------------ data digest

def test_digest_types_ids_and_checks_concepts_against_the_ontology():
    digest = DataDigest()
    assert digest.identifiers("who is ORG-01, and what about APP-002 on CTR-001 for V-001?") == [
        {"id": "ORG-01", "type": "OrganizationUnit"}, {"id": "APP-002", "type": "Application"},
        {"id": "CTR-001", "type": "Contract"}, {"id": "V-001", "type": "Vendor"}]
    assert digest.concept("Application names")["concept"] == "Application.name"
    assert digest.concept("Organization.name") == {"concept": "OrganizationUnit.name", "type": "OrganizationUnit",
                                                   "attribute": "name", "recorded": True, "restricted": False}
    assert digest.concept("Vendor.ceo")["recorded"] is False
    # A related entity is that entity, not a missing attribute of the vendor.
    assert digest.concept("Vendor.Owner") == {"concept": "Owner", "type": "Owner", "attribute": None, "recorded": True, "restricted": False}
    assert digest.concept("Assignment.worker_alias")["restricted"] is True
    # An unrecognised type claims nothing either way.
    assert digest.concept("Spaceship.name")["recorded"] is None
    assert digest.path("Vendor", "Application") == "Vendor -PROVIDES-> Service -SUPPORTS-> Application"
    summary = digest.summary()
    assert any(t["type"] == "OrganizationUnit" and "name" in t["attributes"] for t in summary["types"])
    assert not any(t["type"] == "Dataset" for t in summary["types"])
    assert all("email" not in t["attributes"] for t in summary["types"])


# ------------------------------------------------------------------ the reported conversation

def test_elliptical_follow_up_keeps_the_vendor_in_focus(decision):
    # The model echoes the vendor in focus and mis-scopes the question: it is still the follow-up.
    understood = route(["vendor360"], "Aurelix Codeworks", "dependencies", scope="named", requested=["Application.name"],
                       standalone="What are the names of the applications supported by V-001 (Aurelix Codeworks)?")
    model = ScriptedModel(understood, [call("get_vendor_360", vendor_id="V-001")])
    state = focused()
    result = SupervisorAgent(decision, model).ask("are there any application names?", state=state)
    assert result["status"] == "answered" and result["resolved_entities"]["vendor_ids"] == ["V-001"]
    assert (state.active_vendor_id, state.active_contract_id) == ("V-001", "CTR-001")
    # Without an explorer the names are not in any certified fact: said plainly, with where they live.
    note = next(n for n in result["limitations"] if n["code"] == "requested_not_found")
    assert note["message"] == ("No certified fact or source row returned the name of the application for V-001; nothing is inferred. "
                               "In the data model, Vendor -PROVIDES-> Service -SUPPORTS-> Application.")
    assert result["understanding"]["notes"] == [note["message"]]
    # The writer answers the interpreted question and is told what was not found.
    synthesis = next(p for stage, p, _ in model.requests if stage == "supervisor_synthesis")
    assert synthesis["understanding"]["interpreted_question"].startswith("What are the names of the applications")
    assert synthesis["understanding"]["not_answered"] == [note["message"]]
    # Specialists see the interpretation; parameters are still checked against the user's words.
    plan = next(p for stage, p, _ in model.requests if stage == "vendor360")
    assert plan["question"] == "are there any application names?" and plan["interpreted_question"].endswith("(Aurelix Codeworks)?")


def test_explorer_looks_up_the_requested_names(decision):
    understood = route(["vendor360"], focus="dependencies", active=True, requested=["Application.name"],
                       standalone="What are the names of the applications supported by V-001?")
    names = FixedExecutor({"columns": ["a.application_id", "a.name"], "rows": [["APP-001", "Payment Routing"], ["APP-002", "Card Authorization"]],
                           "truncated": False})
    m = ExplorerModel(understood, [call("get_vendor_360", vendor_id="V-001")],
                      {"vendor360": [step("run_cypher", APPS, graph="business"), step("finish", keep=["Q1"])]})
    agent = SupervisorAgent(decision, m, explorer=explorer(m, cypher=names))
    result = agent.ask("are there any application names?", state=focused())
    assert result["status"] == "answered" and "Payment Routing" in result["final_answer"]
    assert "requested_not_found" not in codes(result)
    task = next(p for stage, p, _ in m.requests if stage == "explore_vendor360")["task"]
    assert task["requested"] == ["Application.name"]
    assert task["paths"] == ["Vendor -PROVIDES-> Service -SUPPORTS-> Application"]
    # Explorers read the standalone question, so the follow-up keeps its subject.
    assert next(p for stage, p, _ in m.requests if stage == "explore_vendor360")["question"].endswith("supported by V-001?")


def test_reference_id_is_never_resolved_as_a_vendor_and_keeps_the_focus(decision):
    # The old failure: 'ORG-01' typed as a vendor became not_found and cleared the focus.
    understood = route(["vendor360"], "ORG-01", requested=["OrganizationUnit.name"], scope="focus",
                       standalone="What is the name of organization ORG-01, the organization of V-001?")
    model = ScriptedModel(understood, [call("get_vendor_360", vendor_id="V-001")])
    state = focused()
    result = SupervisorAgent(decision, model).ask("what is the name of the organization under this vendor? or who is ORG-01", state=state)
    assert result["status"] == "answered" and "vendor_resolution" not in codes(result)
    assert result["understanding"]["entities"] == [{"type": "OrganizationUnit", "mention": "ORG-01"}]
    assert result["resolved_entities"]["references"] == [{"type": "OrganizationUnit", "mention": "ORG-01"}]
    assert result["resolved_entities"]["vendor_ids"] == ["V-001"]
    assert (state.active_vendor_id, state.active_contract_id) == ("V-001", "CTR-001")


def test_reference_question_on_its_own_explains_what_it_needs(decision):
    # 'who is ORG-01?' on its own, with certified tools only: no vendor tool applies, and the stop says why.
    understood = route(["vendor360"], requested=["OrganizationUnit"], standalone="Who is organization ORG-01?")
    state = focused()
    result = SupervisorAgent(decision, ScriptedModel(understood, [])).ask("who is ORG-01", state=state)
    assert result["status"] == "clarification" and result["final_answer"].startswith("This question needs a named vendor")
    assert "I understood the question as: \"Who is organization ORG-01?\"" in result["final_answer"]
    assert "I read ORG-01 as an organization unit" in result["final_answer"]
    assert (state.active_vendor_id, state.active_contract_id) == ("V-001", "CTR-001")
    # With an explorer it is looked up across the source data, anchored on ORG-01.
    org = 'SELECT "Organization_ID", "Organization_Name", _source_line FROM mdm.organization_ou_crosswalk WHERE "Organization_ID" = \'ORG-01\''
    m = ExplorerModel(understood, [], {"vendor360": [step("run_sql", org), step("finish", keep=["Q1"])]})
    result = SupervisorAgent(decision, m, explorer=explorer(m)).ask("who is ORG-01", state=focused())
    assert result["status"] == "answered" and "Payments" in result["final_answer"]
    task = next(p for stage, p, _ in m.requests if stage == "explore_vendor360")["task"]
    assert task["anchors"] == ["ORG-01"] and task["scope"] == "portfolio"


# ------------------------------------------------------------------ explanations

def test_a_reference_listed_as_a_vendor_means_the_focus(decision):
    # Live: the model listed "this vendor" as a vendor name; it is the vendor in focus, never an unknown vendor.
    understood = route(["vendor360"], "this vendor", requested=["Vendor.ceo"], scope="named")
    state = focused()
    result = SupervisorAgent(decision, ScriptedModel(understood, [call("get_vendor_360", vendor_id="V-001")])).ask(
        "who is the CEO of this vendor?", state=state)
    assert result["status"] == "answered" and result["resolved_entities"]["vendor_ids"] == ["V-001"]
    assert "not_recorded" in codes(result) and state.active_vendor_id == "V-001"


def test_data_the_model_does_not_record_is_answered_and_explained(decision):
    understood = route(["vendor360"], "V-001", requested=["Vendor.ceo"], standalone="Who is the CEO of V-001?")
    result = SupervisorAgent(decision, ScriptedModel(understood, [call("get_vendor_360", vendor_id="V-001")])).ask("Who is the CEO of V-001?")
    assert result["status"] == "answered"
    note = next(n for n in result["limitations"] if n["code"] == "not_recorded")
    assert note["message"].startswith("The data model records no 'ceo' for a vendor, so no source holds it and nothing is inferred.")
    assert "legal_name" in note["message"] and "requested_not_found" not in codes(result)


def test_declining_specialists_explain_and_ask(decision):
    understood = route(["vendor360"], focus="dependencies", active=True, requested=["Application.name"],
                       standalone="What are the names of the applications supported by V-001?")
    result = SupervisorAgent(decision, ScriptedModel(understood, [])).ask("are there any application names?", state=focused())
    assert result["status"] == "clarification"
    assert result["understanding"]["reason"] == "specialists_declined"
    assert result["final_answer"] == (
        "I could not turn this question into a valid lookup. I understood the question as: \"What are the names of the applications "
        "supported by V-001?\". The vendor 360 specialist found nothing to look up for it with the certified tools. Do you want the name "
        "of the applications for V-001 (Aurelix Codeworks) (in the data model: Vendor -PROVIDES-> Service -SUPPORTS-> Application), "
        "or for a specific contract or entity? Name it and I will look again.")


def test_unknown_vendor_and_unmentioned_vendor_say_why(decision):
    result = SupervisorAgent(decision, ScriptedModel(route(["vendor360"], "Unknown Vendor"), [])).ask("What about Unknown Vendor?")
    assert result["status"] == "not_found"
    assert "'Unknown Vendor' does not match a vendor ID, contract ID or exact vendor name" in result["final_answer"]
    result = SupervisorAgent(decision, ScriptedModel(route(["vendor360"], "Services"), [])).ask("What do we know about Services?")
    assert result["status"] == "clarification" and "matches more than one vendor" in result["final_answer"]
    assert "Did you mean" in result["understanding"]["clarifying_question"]
    # A vendor the model brought in from elsewhere, not the one in focus, is named in the reason.
    result = SupervisorAgent(decision, ScriptedModel(route(["vendor360"], "V-009"), [])).ask("What about this?", state=focused())
    assert result["status"] == "clarification" and result["understanding"]["reason"] == "mention_not_in_question"
    assert "I took it to be about V-009 (Veylora Application Services)" in result["final_answer"]


def test_unknown_contract_still_clears_the_focus_with_a_reason(decision):
    state = focused()
    result = SupervisorAgent(decision, ScriptedModel(route(["renewal"], focus="renewal"), [])).ask("What about CTR-999?", state=state)
    assert result["status"] == "not_found" and "CTR-999 is not a contract in the canonical master." in result["final_answer"]
    assert (state.active_vendor_id, state.active_contract_id) == (None, None)


def test_a_best_guess_is_answered_with_its_assumption(decision):
    understood = route(["vendor360"], focus="overview", active=True, assumption="Assumed 'it' means V-001, the vendor in focus.")
    result = SupervisorAgent(decision, ScriptedModel(understood, [call("get_vendor_360", vendor_id="V-001")])).ask(
        "tell me more about it", state=focused())
    assert result["status"] == "answered"
    assert {"code": "assumption", "message": "Interpretation: Assumed 'it' means V-001, the vendor in focus."} in result["limitations"]
    assert "Interpretation: Assumed 'it' means V-001" in result["final_answer"]


def test_the_model_s_own_clarifying_question_is_shown(decision):
    understood = {**route(["what_if"], focus="scenario"), "status": "clarification",
                  "clarifying_question": "By what percentage or how many assignments should India contractors change?"}
    result = SupervisorAgent(decision, ScriptedModel(understood, [])).ask("What if we change India contractors?")
    assert result["status"] == "clarification" and result["final_answer"].endswith(
        "By what percentage or how many assignments should India contractors change?")


def test_nothing_found_is_clarified_before_any_deeper_search(decision):
    understood = route(["risk_dependency"], focus="risk", requested=["RiskAssessment.risk_tier"])
    m = ExplorerModel(understood, [])
    result = SupervisorAgent(decision, m, explorer=explorer(m)).ask("What contracts are at major risk?")
    assert result["status"] == "not_found" and result["final_answer"].startswith("No certified or source rows")
    assert result["understanding"]["reason"] == "no_rows"
    assert ("Which vendor or contract should I look at for the risk tier of the third-party risk assessments "
            "(in the data model: Vendor <-ASSESSES- RiskAssessment)?") in result["final_answer"]
    # One round only: the empty result is not re-delegated.
    assert [e["round"] for e in result["trace"] if e["node"] == "explore"] == [0]
    assert [e["node"] for e in result["trace"]][-2:] == ["diagnose", "stop"]
