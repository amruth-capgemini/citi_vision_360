"""Opt-in paid model evaluation; deterministic tools use synthetic graph doubles."""

import os
from pathlib import Path

import pytest

from citi_project.services.agents import OpenAIJsonModel, SupervisorAgent
from citi_project.services.decision_intelligence import DecisionIntelligenceService
from citi_project.services.semantic_service import VendorSemanticService
from citi_project.services.structured_data import StructuredQueryService
from test_semantic_service import canonical_graph


@pytest.mark.openai_integration
@pytest.mark.parametrize("question,specialist,expected", [
    ("Why is V-005 above budget?", "spend_forecast", "294920.56"),
    ("What if India contractors are reduced by 20%?", "what_if", "unavailable"),
    ("What do we know about Aurelix Codeworks?", "vendor360", "Aurelix Codeworks"),
])
def test_live_agent(request, question, specialist, expected):
    if not request.config.getoption("--openai-integration"):
        pytest.skip("Live OpenAI tests require --openai-integration")
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY is not visible in this process")
    structured = StructuredQueryService(Path(__file__).resolve().parents[2] / "initial_plan")
    decision = DecisionIntelligenceService(VendorSemanticService(structured, canonical_graph(structured)))
    model = OpenAIJsonModel()
    try:
        result = SupervisorAgent(decision, model).ask(question)
        assert result["status"] == "answered"
        assert specialist in result["specialists_used"]
        assert expected in result["final_answer"]
        assert not any(n["code"] == "synthesis_fallback" for n in result["limitations"])
    finally:
        model.close()
