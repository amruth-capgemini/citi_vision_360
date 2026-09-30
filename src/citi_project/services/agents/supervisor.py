"""Bounded supervisor -> specialist -> deterministic tools -> grounded synthesis."""

from copy import deepcopy
import json

from .contracts import AgentError, ConversationState, ModelError, ROUTE_SCHEMA, SPECIALISTS, plan_schema, synthesis_schema, validate
from .grounding import build_grounding, model_context, render
from . import prompts
from .tools import ApprovedTools, CanonicalResolver, EntityResolutionError, _mentioned, unsupported_action
from ..structured_data.query_service import NAMESPACE


class SpecialistAgent:
    """One role instance per specialist, each with an independent tool schema."""
    def __init__(self, name, model):
        if name not in SPECIALISTS:
            raise AgentError("Unknown specialist")
        self.name, self.model = name, model

    def plan(self, question, vendor_ids, focus):
        schema = plan_schema(self.name)
        result = validate(self.model.complete(
            self.name, prompts.SPECIALIST,
            {"prompt_version": prompts.PROMPT_VERSION, "specialist": self.name, "question": question,
             "resolved_vendor_ids": vendor_ids, "focus": focus, "allowed_tools": list(SPECIALISTS[self.name])}, schema), schema)
        if result["status"] != "ready" or not result["calls"]:
            raise AgentError("Specialist requires clearer scope or scenario parameters")
        return result["calls"]


class SupervisorAgent:
    def __init__(self, decision_service, model, *, max_tool_calls=8, max_service_calls=12, max_question_chars=2000):
        for value, ceiling in ((max_tool_calls, 8), (max_service_calls, 16), (max_question_chars, 4000)):
            if type(value) is not int or not 1 <= value <= ceiling:
                raise AgentError("Invalid agent bound")
        self.model = model
        self.tools = ApprovedTools(decision_service)
        self.resolver = CanonicalResolver(decision_service.structured)
        self.specialists = {name: SpecialistAgent(name, model) for name in SPECIALISTS}
        self.max_tool_calls, self.max_service_calls = max_tool_calls, max_service_calls
        self.max_question_chars = max_question_chars

    @staticmethod
    def _empty():
        return {"namespace": NAMESPACE, "status": "answered", "intent": None,
                "resolved_entities": {}, "specialists_used": [], "tool_results": [],
                "facts": [], "calculations": [], "flags": [], "evidence": [], "interpretation": [],
                "assumptions": [], "limitations": [], "final_answer": "", "prompt_version": prompts.PROMPT_VERSION}

    @staticmethod
    def _stop(result, status, message, code):
        if result["tool_results"]:
            result.update(build_grounding(result["tool_results"]))
        result["status"], result["final_answer"] = status, message
        result["limitations"].append({"code": code, "message": message})
        return result

    def ask(self, question, *, state=None):
        result = self._empty()
        if not isinstance(question, str) or not question.strip() or len(question) > self.max_question_chars:
            return self._stop(result, "clarification", "Provide a nonempty business question within the input length limit.", "invalid_question")
        if state is not None and not isinstance(state, ConversationState):
            raise AgentError("Expected caller-owned ConversationState")
        if unsupported_action(question):
            return self._stop(result, "unsupported", "This POC supports read-only business context and hypothetical scenarios, not writes or code execution.", "unsupported_action")
        state = state if state is not None else ConversationState()
        try:
            explicit = self.resolver.explicit(question)
            route = validate(self.model.complete("supervisor_route", prompts.SUPERVISOR,
                {"prompt_version": prompts.PROMPT_VERSION, "question": question, "canonical_vendors": self.resolver.catalog,
                 "active_vendor_id": state.active_vendor_id}, ROUTE_SCHEMA), ROUTE_SCHEMA)
            result["intent"] = route["focus"]
            if route["status"] != "route":
                return self._stop(result, route["status"], "Please specify a supported business question and the required vendor or scenario scope.", "route_not_actionable")
            ids = set(explicit)
            for mention in route["entity_mentions"]:
                if not _mentioned(mention, question):
                    raise AgentError("Model entity mention was not present in the question")
                ids.add(self.resolver.resolve(mention))
            if not ids and route["use_active_entity"] and state.active_vendor_id:
                ids.add(self.resolver.resolve(state.active_vendor_id))
            ids = sorted(ids)
            result["resolved_entities"] = {"vendor_ids": ids, "resolution": "canonical exact match or explicit session follow-up"}
            names = route["specialists"]
            if not names or len(names) != len(set(names)):
                raise AgentError("Invalid specialist selection")
            if not ids and set(names) & {"vendor360", "risk_dependency", "spend_forecast"}:
                raise AgentError("A canonical vendor ID or exact name is required")
            # Build and validate ALL plans before executing any service calls.
            prepared = []
            for name in names:
                calls = self.specialists[name].plan(question, ids, route["focus"])
                prepared.extend(self.tools.prepare(name, call, question, ids) for call in calls)
            if len(prepared) > self.max_tool_calls or sum(self.tools.cost(c) for c in prepared) > self.max_service_calls:
                raise AgentError("Request exceeds the tool-call bound")
            if ids and {c["arguments"].get("vendor_id") for c in prepared} != set(ids):
                raise AgentError("Planned tools do not cover all resolved vendors")
            signatures = [json.dumps(c, sort_keys=True) for c in prepared]
            if len(signatures) != len(set(signatures)):
                raise AgentError("Duplicate tool calls are not permitted")
            for call in prepared:
                tool = self.tools.execute(call)
                tool["tool_id"] = f"T{len(result['tool_results']) + 1}"
                result["tool_results"].append(tool)
                if call["specialist"] not in result["specialists_used"]:
                    result["specialists_used"].append(call["specialist"])
        except EntityResolutionError as exc:
            state.active_vendor_id = None
            result["resolved_entities"] = {"candidates": exc.candidates}
            return self._stop(result, exc.status, "Provide a canonical Vendor_ID or an exact, unambiguous vendor name.", "vendor_resolution")
        except AgentError:
            return self._stop(result, "clarification", "The requested entities or tool parameters could not be validated. Specify the vendor and any scenario percentage/count and target explicitly.", "invalid_plan")
        except ModelError:
            # ModelError messages are defined by the adapter, not raw API errors.
            return self._stop(result, "unavailable", "The language-model service is unavailable or returned an incomplete response.", "model_unavailable")
        except Exception:
            return self._stop(result, "unavailable", "A deterministic tool could not complete this request; no missing facts are inferred.", "tool_unavailable")

        grounding = build_grounding(result["tool_results"])
        result.update(grounding)
        schema = synthesis_schema([c["fact_id"] for c in grounding["facts"]])
        try:
            synthesis = validate(self.model.complete("supervisor_synthesis", prompts.SYNTHESIS,
                {"prompt_version": prompts.PROMPT_VERSION, "question": question, "focus": route["focus"], **model_context(grounding)}, schema), schema)
            selected = list(dict.fromkeys(synthesis["selected_fact_ids"]))
            # Every executed tool must be represented; model omission cannot
            # silently discard a specialist's returned context.
            represented = {c["tool_id"] for c in grounding["facts"] if c["fact_id"] in selected}
            for c in grounding["facts"]:
                if c["tool_id"] not in represented:
                    selected.append(c["fact_id"])
                    represented.add(c["tool_id"])
        except (AgentError, ModelError):
            selected = []
            for tool in result["tool_results"]:
                candidates = [c for c in grounding["facts"] if c["tool_id"] == tool["tool_id"]]
                relevant = [c for c in candidates if c["topic"] == route["focus"]]
                selected.extend(c["fact_id"] for c in (relevant or candidates)[:3])
            result["limitations"].append({"code": "synthesis_fallback", "message": "Model synthesis was unavailable or invalid; source-backed statements are rendered directly."})
        # Required coverage prevents a valid but incomplete selection from hiding
        # requested contract listings, scenario baselines or material concerns.
        for tool in result["tool_results"]:
            cards = [c for c in grounding["facts"] if c["tool_id"] == tool["tool_id"]]
            required = set()
            if tool["tool"] == "get_renewal_priorities":
                required = {"renewal"}
            elif tool["tool"] == "get_renewal_context":
                required = {"renewal", "risk", "sla"}
            elif tool["tool"] == "get_vendor_360" and route["focus"] == "overview":
                required = {"overview", "renewal", "spend", "workforce", "dependencies", "risk", "sla"}
            elif tool["tool"] == "get_vendor_dependency_risk":
                required = {"risk"} if route["focus"] == "risk" else {"dependencies", "workforce", "risk", "sla"}
            elif tool["tool"] == "get_spend_forecast_analysis":
                required = {"spend"}
            elif tool["tool"] == "run_workforce_scenario":
                required = {"workforce"} if tool["arguments"]["action"] == "baseline" else {"workforce", "scenario"}
            elif tool["tool"] == "get_vendor_rationalization_opportunities":
                if cards[0]["fact_id"] not in selected:
                    selected.append(cards[0]["fact_id"])
            for card in cards:
                if card["topic"] in required and card["fact_id"] not in selected:
                    selected.append(card["fact_id"])
        result["selected_fact_ids"] = selected
        result["interpretation"] = [{"basis": "POC decision boundary", "text": "Decision context only; no final renewal, consolidation or staffing recommendation."}]
        result["final_answer"] = render(selected, result)
        state.active_vendor_id = ids[0] if len(ids) == 1 else None
        return deepcopy(result)
