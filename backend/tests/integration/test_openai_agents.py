"""Opt-in paid model evaluation; deterministic tools use synthetic graph doubles."""

import os
from pathlib import Path

import pytest

from citi_project.services.agents import ConversationState, SupervisorAgent, select_model
from citi_project.services.decision_intelligence import DecisionIntelligenceService
from citi_project.services.semantic_service import VendorSemanticService
from citi_project.services.structured_data import StructuredQueryService
from test_semantic_service import canonical_graph

QUESTIONS = [
    ("Why is V-005 above budget?", {"spend_forecast"}, "294,920.56"),
    ("What if India contractors are reduced by 20%?", {"what_if"}, "unavailable"),
    ("What do we know about Aurelix Codeworks?", {"vendor360"}, "Aurelix Codeworks"),
    ("Give me renewal, risk and spend context for V-009.", {"renewal", "risk_dependency", "spend_forecast"}, "V-009"),
]


def covered(result):
    """Text of the facts the answer is built on (cited by the model or appended by the host)."""
    return " ".join(c["text"] for c in result["facts"] if c["fact_id"] in result["selected_fact_ids"])


@pytest.fixture
def supervisor(request):
    if not request.config.getoption("--openai-integration"):
        pytest.skip("Live OpenAI tests require --openai-integration")
    azure = (all(os.environ.get(n) for n in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY"))
             and any(os.environ.get(n) for n in ("AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_CHAT_DEPLOYMENT")))
    if not azure and not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("Neither Azure OpenAI nor OpenAI configuration is visible in this process")
    structured = StructuredQueryService(Path(__file__).resolve().parents[3] / "initial_plan")
    decision = DecisionIntelligenceService(VendorSemanticService(structured, canonical_graph(structured)))
    model = select_model()
    try:
        yield SupervisorAgent(decision, model)
    finally:
        model.close()


@pytest.mark.openai_integration
@pytest.mark.parametrize("question,specialists,expected", QUESTIONS)
def test_live_route_stage(supervisor, question, specialists, expected):
    # Checked separately so a failure localizes to routing rather than planning or synthesis.
    route = supervisor.ask(question)["route"]
    assert route is not None and route["status"] == "route", route
    assert specialists <= set(route["specialists"]), route


@pytest.mark.openai_integration
@pytest.mark.parametrize("question,specialists,expected", QUESTIONS)
def test_live_agent(supervisor, question, specialists, expected):
    result = supervisor.ask(question)
    assert result["status"] == "answered", result["route"]
    assert specialists <= set(result["specialists_used"])
    assert expected in covered(result)
    assert not any(n["code"] == "synthesis_fallback" for n in result["limitations"])


@pytest.mark.openai_integration
def test_live_followup_keeps_active_vendor(supervisor):
    state = ConversationState()
    assert supervisor.ask("What do we know about V-005?", state=state)["status"] == "answered"
    assert state.active_vendor_id == "V-005"
    result = supervisor.ask("What about its workforce?", state=state)
    assert result["status"] == "answered", result["route"]
    assert result["resolved_entities"]["vendor_ids"] == ["V-005"]
    assert "3 representative workforce assignments" in covered(result)
