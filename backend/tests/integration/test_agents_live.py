"""Opt-in Phase 3 gate: the exploring supervisor on PostgreSQL, the Aura graphs and Azure OpenAI (all read-only)."""

import logging

import pytest

from citi_project.services.agents import ConversationState

def covered(result):
    """Text of the facts the answer is built on (cited by the model or appended by the host)."""
    return " ".join(c["text"] for c in result["facts"] if c["fact_id"] in result["selected_fact_ids"])


EXPIRING = [f"{v}: contract expires" for v in ("V-005", "V-018", "V-009", "V-007", "V-013", "V-001")]
QUESTIONS = [
    ("What do we know about Aurelix Codeworks?", {"vendor360"}, ["Aurelix Codeworks", "4 representative workforce assignments"]),
    ("Why is V-005 above budget?", {"spend_forecast"}, ["294,920.56"]),
    ("Give me renewal, risk and spend context for V-009.", {"renewal", "risk_dependency", "spend_forecast"},
     ["399,452.04", "risk status Stale"]),
    ("What if India contractors are reduced by 20%?", {"what_if"}, ["25.00%", "Financial impact: unavailable"]),
    ("Which contracts end in the next 90 days, and what forecast and workforce records are linked to them?", {"renewal"}, EXPIRING),
]


@pytest.fixture(scope="module")
def supervisor(request):
    for flag in ("--pg-integration", "--neo4j-integration", "--openai-integration"):
        if not request.config.getoption(flag):
            pytest.skip("The Phase 3 live gate requires --pg-integration --neo4j-integration --openai-integration")
    from citi_project.services.agents.cli import build_live
    logging.getLogger("neo4j").setLevel(logging.ERROR)  # driver notifications about the model's labels
    agent, close = build_live()
    try:
        yield agent
    finally:
        close()


@pytest.mark.pg_integration
@pytest.mark.neo4j_integration
@pytest.mark.openai_integration
@pytest.mark.parametrize("question,specialists,expected", QUESTIONS)
def test_live_exploring_answer(supervisor, question, specialists, expected):
    result = supervisor.ask(question)
    assert result["status"] == "answered", [(e["node"], e["status"]) for e in result["trace"]]
    assert specialists <= set(result["specialists_used"])
    for text in expected:
        assert text in covered(result), text
    assert not any(n["code"] == "synthesis_fallback" for n in result["limitations"]), result["synthesis_checks"]
    assert not any(n["code"] == "contradiction" for n in result["limitations"])
    explored = [e for e in result["trace"] if e["node"] == "explore"]
    assert explored and sum(f["queries_run"] for f in result["findings"]) >= 1


@pytest.mark.pg_integration
@pytest.mark.neo4j_integration
@pytest.mark.openai_integration
def test_live_followup_keeps_active_vendor(supervisor):
    state = ConversationState()
    assert supervisor.ask("What do we know about V-005?", state=state)["status"] == "answered"
    result = supervisor.ask("What about its workforce?", state=state)
    assert result["status"] == "answered" and result["resolved_entities"]["vendor_ids"] == ["V-005"]
    assert "3 representative workforce assignments" in covered(result)
