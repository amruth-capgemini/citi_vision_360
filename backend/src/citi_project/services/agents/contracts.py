"""Local schemas and bounded, caller-owned conversation state."""

from dataclasses import dataclass, field
from typing import Protocol

from jsonschema import Draft202012Validator


class AgentError(ValueError):
    """Sanitized agent boundary error; never contains model/backend payloads."""


class ModelError(RuntimeError):
    pass


class SpecialistDeclined(AgentError):
    """A specialist returned clarification or no calls, as opposed to proposing an invalid call."""


class JsonModel(Protocol):
    def complete(self, stage: str, prompt: str, payload: dict, schema: dict) -> dict: ...


MAX_HISTORY = 6


@dataclass
class ConversationState:
    """One per caller/session: the vendor and contract in focus and a short, bounded memory of recent
    turns (question, resolved IDs, a one-line answer summary). No global memory or credentials."""
    active_vendor_id: str | None = None
    active_contract_id: str | None = None
    history: list = field(default_factory=list)

    def remember(self, question, status, vendor_ids, contract_ids, summary):
        self.history.append({"question": question[:300], "status": status, "vendor_ids": list(vendor_ids),
                             "contract_ids": list(contract_ids), "summary": " ".join(str(summary or "").split())[:300]})
        del self.history[:-MAX_HISTORY]

    def context(self, turns=4):
        """What the router sees of the conversation: IDs in focus and the last few turns (untrusted data)."""
        return {"active_vendor_id": self.active_vendor_id, "active_contract_id": self.active_contract_id,
                "recent_turns": [dict(t) for t in self.history[-turns:]]}


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def nullable(kind, **kwargs):
    return {"type": [kind, "null"], **kwargs}


SPECIALISTS = {
    "vendor360": ("get_vendor_360",),
    "renewal": ("get_renewal_priorities", "get_renewal_context"),
    "risk_dependency": ("get_vendor_dependency_risk",),
    "rationalization": ("get_vendor_rationalization_opportunities",),
    "spend_forecast": ("get_spend_forecast_analysis",),
    "what_if": ("run_workforce_scenario",),
}

# Catalog concepts each specialist needs. A certified tool that returns facts covers
# its specialist's concepts; otherwise explorer rows must, or the gap is reported.
SPECIALIST_CONCEPTS = {
    "vendor360": ("Vendor.vendor_id", "Contract.contract_id", "Contract.end_date"),
    "renewal": ("Contract.contract_id", "Contract.end_date"),
    "risk_dependency": ("RiskAssessment.status", "Application.application_id"),
    "rationalization": ("Product.product_id", "Application.application_id"),
    "spend_forecast": ("Contract.contract_id",),
    "what_if": ("Assignment.assignment_id",),
}
SPECIALIST_OBJECTIVES = {
    "vendor360": "Build the full picture of the vendor: contract, SOW, services, applications, spend, workforce, SLA and risk records.",
    "renewal": "Find renewal context: contract end dates, notice and renewal clauses, and linked forecast and spend records.",
    "risk_dependency": "Find dependency and risk context: applications and services supported, assignments, SLA and risk records.",
    "rationalization": "Find overlap context: shared products, services and applications across vendors.",
    "spend_forecast": "Find spend context: financial and forecast rows linked to the contract, by scenario and month.",
    "what_if": "Find workforce context: assignments by country, worker type, role and the contracts they are under.",
}

# Strict structured output generates properties in order: status is last so the
# model commits to focus, specialists and mentions before deciding actionability.
ROUTE_SCHEMA = obj({
    "focus": {"type": "string", "enum": ["overview", "workforce", "renewal", "risk", "dependencies", "spend", "scenario", "rationalization"]},
    "specialists": {"type": "array", "items": {"type": "string", "enum": list(SPECIALISTS)}, "maxItems": 6},
    "entity_mentions": {"type": "array", "items": {"type": "string", "maxLength": 160}, "maxItems": 4},
    "use_active_entity": {"type": "boolean"},
    "status": {"type": "string", "enum": ["route", "clarification", "unsupported"]},
})

VENDOR = {"type": "string", "pattern": "^V-[0-9]{3}$"}
ORG = nullable("string", pattern="^ORG-[0-9]{2}$")
DATE = nullable("string", pattern="^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
DAYS = nullable("integer", minimum=0, maximum=3650)
TOOL_SCHEMAS = {
    "get_vendor_360": obj({"vendor_id": VENDOR, "as_of_date": DATE}),
    "get_renewal_context": obj({"vendor_id": VENDOR, "days": DAYS, "as_of_date": DATE}),
    "get_renewal_priorities": obj({"days": DAYS, "as_of_date": DATE}),
    "get_vendor_dependency_risk": obj({"vendor_id": VENDOR}),
    "get_vendor_rationalization_opportunities": obj({"organization_id": ORG, "vendor_id": nullable("string", pattern="^V-[0-9]{3}$")}),
    "get_spend_forecast_analysis": obj({"vendor_id": VENDOR, "year": nullable("integer", minimum=2000, maximum=2100)}),
    "run_workforce_scenario": obj({
        "action": {"type": "string", "enum": ["baseline", "reduce", "shift"]},
        "percentage": nullable("number", minimum=0, maximum=100),
        "assignment_count": nullable("integer", minimum=0, maximum=10000),
        "vendor_id": nullable("string", pattern="^V-[0-9]{3}$"), "organization_id": ORG,
        "country": nullable("string", maxLength=80), "market": nullable("string", maxLength=80),
        "worker_type": nullable("string", enum=[None, "Employee", "Contractor", "Consultant"]),
        "target_country": nullable("string", maxLength=80),
        "target_worker_type": nullable("string", enum=[None, "Employee", "Contractor", "Consultant"]),
        "assignment_ids": {"type": ["array", "null"], "items": {"type": "string", "pattern": "^ASN-[0-9]{3}-[0-9]{3}$"}, "maxItems": 60},
    }),
}


def plan_schema(specialist):
    return obj({"status": {"type": "string", "enum": ["ready", "clarification"]},
                "calls": {"type": "array", "maxItems": 4, "items": {"anyOf": [
                    obj({"name": {"type": "string", "enum": [name]}, "arguments": TOOL_SCHEMAS[name]})
                    for name in SPECIALISTS[specialist]]}}})


def synthesis_schema(card_ids):
    """The model writes the answer; every paragraph cites the fact cards its values come from (checked in code)."""
    return obj({
        "summary": {"type": "string", "maxLength": 700},
        "paragraphs": {"type": "array", "minItems": 1, "maxItems": 12, "items": obj({
            "heading": {"type": "string", "maxLength": 80},
            "text": {"type": "string", "maxLength": 1200},
            "fact_ids": {"type": "array", "minItems": 1, "maxItems": 12, "items": {"type": "string", "enum": card_ids}},
        })},
    })


def validate(value, schema):
    if not isinstance(value, dict) or not Draft202012Validator(schema).is_valid(value):
        raise AgentError("Model output failed its approved schema")
    return value
