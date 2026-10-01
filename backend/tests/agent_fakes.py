"""Scripted model outputs: these are contract tests, not live routing evaluations."""

from copy import deepcopy

from citi_project.services.agents.contracts import TOOL_SCHEMAS


def call(name, **args):
    return {"name": name, "arguments": {**dict.fromkeys(TOOL_SCHEMAS[name]["properties"]), **args}}


def narrative(*fact_ids, text="The returned records are summarised here.", summary="Summary of the returned records."):
    return {"summary": summary, "paragraphs": [{"heading": "Findings", "text": text, "fact_ids": list(fact_ids)}]}


def route(specialists, mention=None, focus="overview", active=False):
    return {"status": "route", "specialists": specialists, "entity_mentions": [mention] if mention else [],
            "use_active_entity": active, "focus": focus}


DEMOS = [
    ("What do we know about V-001?", route(["vendor360"], "V-001"), [call("get_vendor_360", vendor_id="V-001")]),
    ("Which contracts expire in the next 90 days?", route(["renewal"], focus="renewal"), [call("get_renewal_priorities", days=90)]),
    ("How dependent are we on V-009?", route(["risk_dependency"], "V-009", "dependencies"), [call("get_vendor_dependency_risk", vendor_id="V-009")]),
    ("What is missing for V-017?", route(["risk_dependency"], "V-017", "risk"), [call("get_vendor_dependency_risk", vendor_id="V-017")]),
    ("Where can we simplify the vendor footprint?", route(["rationalization"], focus="rationalization"), [call("get_vendor_rationalization_opportunities")]),
    ("Find potential rationalization candidates in ORG-01.", route(["rationalization"], focus="rationalization"), [call("get_vendor_rationalization_opportunities", organization_id="ORG-01")]),
    ("Why is V-005 above budget?", route(["spend_forecast"], "V-005", "spend"), [call("get_spend_forecast_analysis", vendor_id="V-005", year=2026)]),
    ("Show V-009 actual vs YTD budget.", route(["spend_forecast"], "V-009", "spend"), [call("get_spend_forecast_analysis", vendor_id="V-009")]),
    ("How many India assignments do we have?", route(["what_if"], focus="workforce"), [call("run_workforce_scenario", action="baseline", percentage=0, country="India")]),
    ("What if India contractors are reduced by 20%?", route(["what_if"], focus="scenario"), [call("run_workforce_scenario", action="reduce", percentage=20, country="India", worker_type="Contractor")]),
    ("What if two India contractors are shifted to consultants?", route(["what_if"], focus="scenario"), [call("run_workforce_scenario", action="shift", assignment_count=2, country="India", worker_type="Contractor", target_worker_type="Consultant")]),
    ("Give me renewal, risk and spend context for V-009.", route(["renewal", "risk_dependency", "spend_forecast"], "V-009"), [call("get_renewal_context", vendor_id="V-009"), call("get_vendor_dependency_risk", vendor_id="V-009"), call("get_spend_forecast_analysis", vendor_id="V-009")]),
    ("What do we know about Aurelix Codeworks?", route(["vendor360"], "Aurelix Codeworks"), [call("get_vendor_360", vendor_id="V-001")]),
    ("What about its workforce?", route(["vendor360"], focus="workforce", active=True), [call("get_vendor_360", vendor_id="V-005")]),
    ("What do we know about Services?", route(["vendor360"], "Services"), []),
]


class ScriptedModel:
    def __init__(self, route_result, calls, *, synthesis=None):
        self.route_result, self.calls = route_result, calls
        self.synthesis = synthesis
        self.requests = []

    def complete(self, stage, prompt, payload, schema):
        self.requests.append((stage, deepcopy(payload), deepcopy(schema)))
        if stage == "supervisor_route":
            return deepcopy(self.route_result)
        if stage == "supervisor_synthesis":
            if self.synthesis is not None:
                return deepcopy(self.synthesis)
            # A compliant writer: one paragraph per chosen card, its values copied from that card.
            facts = payload["facts"]
            selected = []
            for tool_id in dict.fromkeys(c["tool_id"] for c in facts):
                choices = [c for c in facts if c["tool_id"] == tool_id]
                focused = [c for c in choices if c["topic"] == payload["focus"]] if payload["focus"] != "overview" else []
                selected.extend((focused or choices)[:8])
            return {"summary": "Here is what the returned records show.",
                    "paragraphs": [{"heading": c["topic"].title(), "text": c["text"], "fact_ids": [c["fact_id"]]} for c in selected[:12]]}
        return {"status": "ready", "calls": [deepcopy(c) for c in self.calls if c["name"] in payload["allowed_tools"]]}
