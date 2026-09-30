"""Local schemas and bounded, caller-owned conversation state."""

from dataclasses import dataclass
from typing import Protocol

from jsonschema import Draft202012Validator


class AgentError(ValueError):
    """Sanitized agent boundary error; never contains model/backend payloads."""


class ModelError(RuntimeError):
    pass


class JsonModel(Protocol):
    def complete(self, stage: str, prompt: str, payload: dict, schema: dict) -> dict: ...


@dataclass
class ConversationState:
    """One per caller/session. No global memory, transcripts or credentials."""
    active_vendor_id: str | None = None


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

ROUTE_SCHEMA = obj({
    "status": {"type": "string", "enum": ["route", "clarification", "unsupported"]},
    "specialists": {"type": "array", "items": {"type": "string", "enum": list(SPECIALISTS)}, "maxItems": 6},
    "entity_mentions": {"type": "array", "items": {"type": "string", "maxLength": 160}, "maxItems": 4},
    "use_active_entity": {"type": "boolean"},
    "focus": {"type": "string", "enum": ["overview", "workforce", "renewal", "risk", "dependencies", "spend", "scenario", "rationalization"]},
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
    "get_vendor_rationalization_opportunities": obj({"organization_id": ORG}),
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
    return obj({"selected_fact_ids": {"type": "array", "minItems": 1, "maxItems": 12,
                                       "items": {"type": "string", "enum": card_ids}}})


def validate(value, schema):
    if not isinstance(value, dict) or not Draft202012Validator(schema).is_valid(value):
        raise AgentError("Model output failed its approved schema")
    return value
