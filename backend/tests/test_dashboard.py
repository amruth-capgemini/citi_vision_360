"""Vendor 360 dashboard: attention states, review prompts, totals and the cached endpoint."""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from citi_project.api.dashboard import build_dashboard
from citi_project.services.structured_data import StructuredQueryService

DATA = Path(__file__).resolve().parents[2] / "initial_plan"
# Renewal clause terms as stored in the graph for two demo contracts.
RENEWALS = {"CTR-005": {"notice_deadline": "2026-07-15", "notice_days": 90, "automatic_renewal": False, "renewal_extension_months": 0},
            "CTR-009": {"notice_deadline": "2026-10-13", "notice_days": 30, "automatic_renewal": False, "renewal_extension_months": 0}}


@pytest.fixture(scope="module")
def board():
    if not DATA.exists():
        pytest.skip("initial_plan data pack is absent")
    structured = StructuredQueryService(DATA)
    return build_dashboard(structured.list_forecast_records(), RENEWALS, as_of=structured.as_of_date,
                           scanned_at=datetime(2026, 9, 28, tzinfo=timezone.utc))


def contract(board, contract_id):
    return next(c for c in board["contracts"] if c["contract_id"] == contract_id)


def test_renewal_notice_drives_the_attention_state(board):
    assert board["as_of_date"] == "2026-09-28" and board["totals"]["contracts"] == 20
    overdue = contract(board, "CTR-005")
    assert (overdue["attention"], overdue["renewal_decision_date"], overdue["days_to_decision"]) == ("past_notice", "2026-07-15", -75)
    due = contract(board, "CTR-009")
    assert (due["attention"], due["days_to_decision"]) == ("notice_due", 15)
    # Without a renewal clause the expiry date still raises attention.
    assert contract(board, "CTR-001")["attention"] == "expiring" and contract(board, "CTR-001")["renewal_decision_date"] is None


def test_review_items_explain_why_and_never_invent_a_risk_tier(board):
    review = {r["contract_id"]: r for r in board["review"]}
    aurelix = review["CTR-001"]
    assert aurelix["priority"] == "high" and aurelix["status"] == "draft"
    assert any(r.startswith("SLA breach in 2026-08") for r in aurelix["reasons"]) and "Contract risk tier: High" in aurelix["reasons"]
    missing = contract(board, "CTR-017")
    assert missing["risk_assessment_status"] == "Missing" and missing["risk_tier"] is None
    assert any("no risk tier is inferred" in a for a in review["CTR-017"]["actions"])
    assert board["review"][0]["priority"] == "high" and board["totals"]["in_review"] == len(board["review"])


def test_totals_add_up_from_the_contract_rows(board):
    totals = board["totals"]
    assert Decimal(totals["budget_2026"]) == sum(Decimal(c["budget_2026"]) for c in board["contracts"])
    assert totals["past_notice"] == 1 and totals["decisions_due"] == 1
    assert totals["sla_breaches"] == sum(c["sla"]["breach"] for c in board["contracts"]) >= 1
    assert board["sources"] == ["finance.ct_vendor_technology_forecast", "graph :HAS_CLAUSE RenewalClause"]


def test_dashboard_endpoint_caches_until_refresh():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from citi_project.api import create_app

    class Runtime:
        calls = []

        def dashboard(self, *, refresh=False):
            self.calls.append(refresh)
            return {"totals": {"contracts": 20}}

        def close(self):
            pass

    runtime = Runtime()
    client = TestClient(create_app(runtime))
    assert client.get("/api/dashboard").json()["totals"]["contracts"] == 20
    client.get("/api/dashboard", params={"refresh": "true"})
    assert runtime.calls == [False, True]
