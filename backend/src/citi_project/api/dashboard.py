"""Vendor 360 dashboard: a read-only portfolio scan of renewal notice, spend, risk and SLA.

Contract rows come from the forecast table (finance.ct_vendor_technology_forecast); renewal
notice terms come from each contract's RenewalClause in the business graph. Review items
point a human at contracts that need attention; they are review prompts, not decisions.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

from ..services.structured_data.query_service import decimal_string, decimal_value, variance

ATTENTION_DAYS = 90
HIGH_RISK = {"High", "Critical"}
OVER_BUDGET_PCT = Decimal("5")
FORECAST_SOURCE = "finance.ct_vendor_technology_forecast"
RENEWAL_SOURCE = "graph :HAS_CLAUSE RenewalClause"

_RENEWALS = """// citi-api:dashboard-renewal-terms
MATCH (c:Contract {_kg_namespace: $namespace})-[:HAS_CLAUSE]->(r {_kg_namespace: $namespace})
WHERE r._kg_class = 'RenewalClause'
RETURN c.contract_id AS contract_id, r.notice_deadline AS notice_deadline, r.notice_days AS notice_days,
       r.automatic_renewal AS automatic_renewal, r.renewal_extension_months AS renewal_extension_months
ORDER BY contract_id"""


def read_renewal_terms(client, namespace):
    """{contract_id: renewal clause terms} for every contract in the namespace."""
    rows = client.read(lambda tx: list(tx.run(_RENEWALS, namespace=namespace)))
    return {r["contract_id"]: {k: r[k] for k in ("notice_deadline", "notice_days", "automatic_renewal", "renewal_extension_months")}
            for r in rows if r["contract_id"]}


def _day(value):
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _money(value):
    try:
        return decimal_value(value) if value not in (None, "") else None
    except ValueError:
        return None


def _attention(end, notice, as_of):
    if end is not None and end < as_of:
        return "expired"
    if notice is not None and notice < as_of:
        return "past_notice"
    if notice is not None and (notice - as_of).days <= ATTENTION_DAYS:
        return "notice_due"
    if end is not None and (end - as_of).days <= ATTENTION_DAYS:
        return "expiring"
    return "on_track"


def _contract(row, terms, as_of):
    end, notice = _day(row.get("Contract_End_Date")), _day(terms.get("notice_deadline"))
    budget, forecast, actual = (_money(row.get(k)) for k in ("Budget_2026_USD", "Forecast_2026_USD", "Actual_YTD_2026_USD"))
    status = row.get("Risk_Assessment_Status") or None
    breach = str(row.get("Source_SLA_Breach", "")).lower() == "true"
    over = variance(forecast, budget) if forecast is not None and budget else None
    return {
        "vendor_id": row["Vendor_ID"], "vendor_name": row.get("Vendor_Name"), "contract_id": row["Contract_ID"],
        "description": row.get("Description"), "organization": row.get("OU"),
        "end_date": end.isoformat() if end else None, "days_to_expiry": (end - as_of).days if end else None,
        "renewal_decision_date": notice.isoformat() if notice else None, "days_to_decision": (notice - as_of).days if notice else None,
        "notice_days": terms.get("notice_days"), "automatic_renewal": terms.get("automatic_renewal"),
        "attention": _attention(end, notice, as_of),
        # A missing assessment carries no tier; none is inferred.
        "risk_tier": (row.get("Risk_Tier") or None) if status != "Missing" else None,
        "risk_assessment_status": status, "risk_assessment_date": row.get("Risk_Assessment_Date") or None,
        "vrm_status": row.get("VRM_Status") or None,
        "sla": {"period": row.get("Source_SLA_Period") or None, "actual_percent": row.get("Source_SLA_Actual_Percent") or None,
                "target_percent": row.get("Source_SLA_Target_Percent") or None, "breach": breach},
        "budget_2026": decimal_string(budget) if budget is not None else None,
        "forecast_2026": decimal_string(forecast) if forecast is not None else None,
        "actual_ytd_2026": decimal_string(actual) if actual is not None else None,
        "forecast_variance": over,
        "warning": row.get("WARNING") or None,
        "source": {"dataset": FORECAST_SOURCE, "record_id": row.get("record_id"), "renewal_terms": RENEWAL_SOURCE if terms else None},
    }


def _review(c):
    """Why a human should look at this contract, most urgent first. Empty when nothing stands out."""
    reasons, actions = [], []
    if c["attention"] == "past_notice":
        reasons.append(f"Renewal notice deadline passed on {c['renewal_decision_date']}")
        actions.append("Confirm the renewal position now that the notice deadline has passed.")
    elif c["attention"] == "notice_due":
        reasons.append(f"Renewal decision due {c['renewal_decision_date']} ({c['days_to_decision']} days)")
        actions.append("Prepare the renewal decision before the notice deadline.")
    elif c["attention"] == "expiring":
        reasons.append(f"Contract expires {c['end_date']} ({c['days_to_expiry']} days)")
        actions.append("Confirm whether the contract should be renewed before it expires.")
    sla = c["sla"]
    if sla["breach"]:
        reasons.append(f"SLA breach in {sla['period']}: {sla['actual_percent']}% against a {sla['target_percent']}% target")
        actions.append("Start an SLA remediation review.")
    if c["risk_tier"] in HIGH_RISK:
        reasons.append(f"Contract risk tier: {c['risk_tier']}")
        actions.append("Review the risk position and mitigations.")
    if c["risk_assessment_status"] and c["risk_assessment_status"] != "Current":
        reasons.append(f"Risk assessment is {c['risk_assessment_status'].lower()}")
        actions.append("Obtain a current risk assessment; no risk tier is inferred.")
    over = c["forecast_variance"]
    if over and over["percent"] is not None and Decimal(over["percent"]) > OVER_BUDGET_PCT:
        reasons.append(f"2026 forecast is {over['percent']}% over budget")
        actions.append("Review spend against budget.")
    if not reasons:
        return None
    urgent = c["attention"] in ("past_notice", "expired") or sla["breach"] or c["risk_tier"] in HIGH_RISK \
        or c["risk_assessment_status"] == "Missing"
    return {"vendor_id": c["vendor_id"], "vendor_name": c["vendor_name"], "contract_id": c["contract_id"],
            "priority": "high" if urgent else "medium", "status": "draft", "reasons": reasons, "actions": actions,
            "headline": actions[0]}


def build_dashboard(forecast, renewals, *, as_of, scanned_at=None):
    """forecast: StructuredQueryService.list_forecast_records(); renewals: read_renewal_terms()."""
    as_of = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of))
    contracts = [_contract(row, renewals.get(row["Contract_ID"], {}), as_of) for row in forecast["facts"]["records"]]
    contracts.sort(key=lambda c: (c["renewal_decision_date"] or c["end_date"] or "9999", c["contract_id"]))
    review = [item for item in map(_review, contracts) if item]
    review.sort(key=lambda r: (r["priority"] != "high", -len(r["reasons"]), r["contract_id"]))

    def total(field):
        values = [Decimal(c[field]) for c in contracts if c[field] is not None]
        return decimal_string(sum(values, Decimal(0)))

    budget, forecast_total = Decimal(total("budget_2026")), Decimal(total("forecast_2026"))
    count = lambda test: sum(1 for c in contracts if test(c))  # noqa: E731
    return {
        "as_of_date": as_of.isoformat(),
        "scanned_at": (scanned_at or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "attention_days": ATTENTION_DAYS,
        "totals": {
            "vendors": len({c["vendor_id"] for c in contracts}), "contracts": len(contracts),
            "budget_2026": decimal_string(budget), "forecast_2026": decimal_string(forecast_total),
            "actual_ytd_2026": total("actual_ytd_2026"),
            "forecast_variance": variance(forecast_total, budget) if budget else None,
            "past_notice": count(lambda c: c["attention"] == "past_notice"),
            "decisions_due": count(lambda c: c["attention"] == "notice_due"),
            "expiring": count(lambda c: c["days_to_expiry"] is not None and 0 <= c["days_to_expiry"] <= ATTENTION_DAYS),
            "high_risk": count(lambda c: c["risk_tier"] in HIGH_RISK),
            "missing_assessment": count(lambda c: c["risk_assessment_status"] == "Missing"),
            "sla_breaches": count(lambda c: c["sla"]["breach"]),
            "over_budget": count(lambda c: c["forecast_variance"] and c["forecast_variance"]["percent"] is not None
                                 and Decimal(c["forecast_variance"]["percent"]) > 0),
            "in_review": len(review),
        },
        "contracts": contracts,
        "review": review,
        "sources": [FORECAST_SOURCE, RENEWAL_SOURCE],
        "limitations": forecast.get("limitations", []) + [
            {"code": "review_prompts", "message": "Review items are prompts for a human reviewer, not renewal, consolidation or staffing decisions."},
            {"code": "financial_periods", "message": "Budget and Forecast are full-year 2026; Actual is January-August 2026."}],
    }
