"""LangGraph orchestration of the supervisor, specialists, certified tools and explorers.

    guard -> understand -> resolve -> plan (one node per specialist, in parallel) -> validate
          -> execute (certified tools) -> explore (one explorer per specialist, in parallel)
          -> verify (re-delegates concept gaps once) -> diagnose -> ground -> synthesize

``understand`` reads the question against the data model (the ontology digest) and the
conversation: typed entities, the scope, what is asked for and a standalone rewrite.
``diagnose`` checks what was asked for against what came back and the data model: data
the model does not record is reported, not invented; when nothing came back at all the
question is clarified, with the reason, before any deeper search.

Early stops go to a single ``stop`` node, which explains what was understood, why the
question could not proceed and what is missing. Without an explorer the explore and
verify stages are skipped, so behaviour is exactly the certified path.
"""

from copy import deepcopy
import json
import operator
import re
import time
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from . import prompts
from .contracts import (AgentError, ConversationState, INTENT_SCHEMA, ModelError, SPECIALISTS, SPECIALIST_CONCEPTS, SPECIALIST_OBJECTIVES,
                        SpecialistDeclined, synthesis_schema, validate)
from .grounding import build_grounding, model_context, render, render_narrative, verify_narrative
from .tools import EntityResolutionError, _mentioned, unsupported_action
from ..catalog.ontology import CATALOG_NAMESPACE
from ..structured_data.query_service import NAMESPACE

MESSAGES = {
    "invalid_question": ("clarification", "Provide a nonempty business question within the input length limit."),
    "unsupported_action": ("unsupported", "This POC supports read-only business context and hypothetical scenarios, not writes or code execution."),
    "route_not_actionable": (None, "Please specify a supported business question and the required vendor or scenario scope."),
    "vendor_resolution": (None, "Provide a canonical Vendor_ID or Contract_ID, or an exact, unambiguous vendor name."),
    "invalid_plan": ("clarification", "I could not turn this question into a valid lookup."),
    "vendor_required": ("clarification", "This question needs a named vendor when source exploration is off. Name a vendor (e.g. V-001), "
                        "or ask a portfolio question such as contract expiries."),
    "no_evidence": ("not_found", "No certified or source rows were found for this question; nothing is inferred."),
    "model_unavailable": ("unavailable", "The language-model service is unavailable or returned an incomplete response."),
    "tool_unavailable": ("unavailable", "A deterministic tool could not complete this request; no missing facts are inferred."),
}
HOME_DATASET = "clm.canonical_vendor_master"
# A question about the conversation itself, e.g. "which vendor are we talking about here?"
CONTEXT_QUESTION = re.compile(r"\b(which|what|who)\b[^?]{0,40}\b(vendor|supplier|contract)\b[^?]{0,40}"
                              r"\b(talking|talkin|discussing|referring|speaking|looking at|in context|current|active|here|this)\b", re.I)
# An explicit reference to the entity already in focus. 'their' is left to the router: "which vendors
# breached their SLAs?" is a portfolio question, not a follow-up.
FOLLOW_UP = re.compile(r"\b(this|that|the same|same)\s+(vendor|supplier|contract|agreement|company)\b|\bits\b", re.I)
# A mention that only points back at the focus ('this vendor', 'it', 'them') is a reference, not a vendor name.
REFERENCE = re.compile(r"^(?:(?:this|that|the|same|the same|our|current|active)\s+)?(?:vendor|supplier|company|contract|agreement|one)s?$"
                       r"|^(?:it|its|they|them|their|this|that)$", re.I)
VENDOR_SCOPED = ("vendor360", "risk_dependency", "spend_forecast")
# Why a question could not proceed, in words; detail is host-generated (an ID, a parameter, specialist names).
REASONS = {
    "mention_not_in_question": "I took it to be about {detail}, but that vendor is not named in the question and is not the vendor in focus.",
    "specialists_declined": "{detail} found nothing to look up for it with the certified tools.",
    "parameter_not_given": "Answering it needs {detail}, which the question does not state.",
    "scope_not_preserved": "The plan did not keep the scope you gave ({detail}).",
    "tool_not_permitted": "The plan asked a specialist to use a tool it is not permitted to use.",
    "plan_incomplete": "The plan did not cover every vendor in the question.",
    "too_broad": "Answering it would take more tool calls than one question is allowed.",
    "unknown_vendor": "'{detail}' does not match a vendor ID, contract ID or exact vendor name in the canonical catalog.",
    "ambiguous_vendor": "'{detail}' matches more than one vendor.",
    "unknown_contract": "{detail} is not a contract in the canonical master.",
    "no_rows": "No certified tool or source query returned anything for it.",
}


SPECIALIST_LABELS = {"vendor360": "The vendor 360 specialist", "renewal": "The renewal specialist",
                     "risk_dependency": "The risk and dependency specialist", "rationalization": "The rationalization specialist",
                     "spend_forecast": "The spend and forecast specialist", "what_if": "The scenario specialist"}


def _names(specialists):
    labels = [SPECIALIST_LABELS.get(n, n) for n in specialists]
    if len(labels) > 1:
        labels = [labels[0]] + [label[0].lower() + label[1:] for label in labels[1:]]
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


# Field names too generic to say whose attribute they are.
GENERIC_FIELDS = frozenset({"name", "status", "id", "type", "role", "period", "date", "value"})


def _keys(value, into):
    """Every dict key in a nested fact structure, case-folded."""
    if isinstance(value, dict):
        for key, item in value.items():
            into.add(str(key).casefold())
            _keys(item, into)
    elif isinstance(value, list):
        for item in value:
            _keys(item, into)
    return into


def _stop(code, status=None, **extra):
    default, message = MESSAGES[code]
    return {"status": status or default, "message": message, "code": code, **extra}


def _failure(exc):
    if isinstance(exc, EntityResolutionError):
        return _stop("vendor_resolution", exc.status, candidates=exc.candidates, reason=exc.reason, detail=exc.detail)
    if isinstance(exc, AgentError):
        return _stop("invalid_plan", reason=exc.reason, detail=exc.detail)
    if isinstance(exc, ModelError):
        # ModelError messages are defined by the adapter, not raw API errors.
        return _stop("model_unavailable")
    return _stop("tool_unavailable")


class AskInput(TypedDict, total=False):
    """What a caller supplies. From LangGraph Studio only `question` (and an optional active vendor) is needed."""
    question: str
    active_vendor_id: str | None
    session: Any
    result: dict


class AskOutput(TypedDict):
    final_answer: str
    result: dict
    trace: list


class RunState(TypedDict, total=False):
    question: str
    active_vendor_id: str | None
    session: Any
    result: dict
    final_answer: str
    explicit: list
    understanding: dict
    ids: list
    contracts: list
    specialists: list
    explore_only: list
    notes: list
    plans: Annotated[list, operator.add]
    prepared: list
    findings: Annotated[list, operator.add]
    trace: Annotated[list, operator.add]
    gaps: dict
    stop: dict


def _node(name, fn, *, parallel=False):
    """Time a node, record a trace entry, and turn failures into a stop (serial nodes only)."""
    def run(state):
        started = time.perf_counter()
        try:
            update = fn(state) or {}
        except Exception as exc:
            if parallel:
                raise
            update = {"stop": _failure(exc)}
        entry = {"node": name, "status": "stop" if update.get("stop") else "ok",
                 "duration_ms": round((time.perf_counter() - started) * 1000), **update.pop("trace_detail", {})}
        update["trace"] = update.get("trace", []) + [entry]
        return update
    return run


class Orchestrator:
    def __init__(self, agent):
        self.agent = agent
        g = StateGraph(RunState, input_schema=AskInput, output_schema=AskOutput)
        for name, fn in (("guard", self.guard), ("understand", self.understand), ("resolve", self.resolve), ("validate", self.validate),
                         ("execute", self.execute), ("verify", self.verify), ("diagnose", self.diagnose), ("ground", self.ground),
                         ("synthesize", self.synthesize), ("stop", self.stop)):
            g.add_node(name, _node(name, fn))
        # One planning node and one explorer sub-graph per specialist, so each sub-agent is its own
        # node in the graph (and in LangGraph Studio). The supervisor dispatches to them with Send.
        plans = [f"plan_{name}" for name in SPECIALISTS]
        explorers = [f"explore_{name}" for name in SPECIALISTS] if agent.explorer is not None else []
        for node in plans:
            g.add_node(node, _node("plan", self.plan, parallel=True))
            g.add_edge(node, "validate")
        for node in explorers:
            g.add_node(node, agent.explorer.graph)
            g.add_edge(node, "verify")
        g.add_edge(START, "guard")
        g.add_conditional_edges("guard", self._next("understand"), ["understand", "stop"])
        g.add_conditional_edges("understand", self._next("resolve"), ["resolve", "stop"])
        g.add_conditional_edges("resolve", self.dispatch_plans, [*plans, "validate", "stop"])
        g.add_conditional_edges("validate", self._next("execute"), ["execute", "stop"])
        g.add_conditional_edges("execute", self.dispatch_explorers, [*explorers, "diagnose", "stop"])
        g.add_conditional_edges("verify", self.dispatch_gaps, [*explorers, "diagnose"])
        g.add_conditional_edges("diagnose", self._next("ground"), ["ground", "stop"])
        g.add_conditional_edges("ground", self._next("synthesize"), ["synthesize", "stop"])
        g.add_conditional_edges("synthesize", lambda state: "stop" if state.get("stop") else END, ["stop", END])
        g.add_edge("stop", END)
        # Explorer sub-graphs count their own steps; the ceiling leaves room for re-delegation.
        self.graph = g.compile().with_config(recursion_limit=120)

    def invoke(self, question, session, result):
        final = self.graph.invoke({"question": question, "session": session, "result": result})
        result = final["result"]
        result["trace"] = final.get("trace", [])
        return result

    def stream(self, question, session, result):
        """Yield ("node", trace entry) as each stage finishes, ("step", explorer step) as each explorer
        step finishes, and finally ("result", result): the same result ``invoke`` returns."""
        final = None
        for namespace, mode, data in self.graph.stream({"question": question, "session": session, "result": result},
                                                       stream_mode=["updates", "values", "custom"], subgraphs=True):
            if mode == "custom":
                # Explorer sub-graphs publish each step as it finishes (explorer._emit).
                if isinstance(data, dict) and "explore_step" in data:
                    yield "step", data["explore_step"]
            elif mode == "values":
                if not namespace:
                    final = data
            elif not namespace:
                for update in (data or {}).values():
                    for entry in (update or {}).get("trace") or ():
                        yield "node", entry
        result = final["result"]
        result["trace"] = final.get("trace", [])
        yield "result", result

    @staticmethod
    def _next(target):
        return lambda state: "stop" if state.get("stop") else target

    # Supervisor stages -----------------------------------------------------

    def guard(self, state):
        # SupervisorAgent.ask supplies session and result; a Studio run supplies only the question.
        start = {"result": state.get("result") or self.agent._empty(),
                 "session": state.get("session") or ConversationState(state.get("active_vendor_id"))}
        question = state.get("question")
        if not isinstance(question, str) or not question.strip() or len(question) > self.agent.max_question_chars:
            return {**start, "stop": _stop("invalid_question")}
        if unsupported_action(question):
            return {**start, "stop": _stop("unsupported_action")}
        return start

    def understand(self, state):
        agent, question, result, session = self.agent, state["question"], state["result"], state["session"]
        explicit = agent.resolver.explicit(question)
        intent = validate(agent.model.complete("supervisor_understand", prompts.UNDERSTAND,
            {"prompt_version": prompts.PROMPT_VERSION, "question": question, "canonical_vendors": agent.resolver.catalog,
             "data_model": agent.digest.summary(), "ids_in_question": agent.digest.identifiers(question),
             "conversation": session.context()},
            INTENT_SCHEMA), INTENT_SCHEMA)
        # The validated intent is kept for diagnosis of routing decisions.
        result["intent"], result["route"] = intent["focus"], deepcopy(intent)
        understanding = self._understanding(question, intent)
        result["understanding"] = understanding
        detail = {"trace_detail": {"agent": "supervisor", "specialists": intent["specialists"], "focus": intent["focus"],
                                   "route_status": intent["status"], "scope": intent["scope"],
                                   "understood": understanding["standalone_question"],
                                   "requested": [r["concept"] for r in understanding["requested"]],
                                   "entities": [f"{e['type']}:{e['mention']}" for e in understanding["entities"]]}}
        if intent["status"] != "route":
            return {"stop": _stop("route_not_actionable", intent["status"]), "understanding": understanding, **detail}
        return {"explicit": explicit, "understanding": understanding, **detail}

    def _understanding(self, question, intent):
        """The model's reading, with every entity typed against the data model: an ID's format decides its
        type over the model's label, and IDs the model left out are added from the question."""
        digest, entities, seen = self.agent.digest, [], set()
        for entity in intent["entities"]:
            mention = " ".join(entity["mention"].split())
            by_format = digest.classify(mention)
            kind = by_format[0] if by_format else digest.entity_type(entity["type"]) or entity["type"]
            if mention and mention.casefold() not in seen:
                seen.add(mention.casefold())
                entities.append({"type": kind, "mention": mention})
        for item in digest.identifiers(question):
            if item["id"].casefold() not in seen:
                seen.add(item["id"].casefold())
                entities.append({"type": item["type"], "mention": item["id"]})
        requested = list({r["concept"]: r for r in map(digest.concept, intent["requested"])}.values())
        return {"standalone_question": " ".join(intent["standalone_question"].split()) or question, "scope": intent["scope"],
                "entities": entities, "requested": requested, "assumption": intent["assumption"],
                "clarifying_question": intent["clarifying_question"], "notes": []}

    def resolve(self, state):
        agent, question, result = self.agent, state["question"], state["result"]
        understanding = state["understanding"]
        ids = set(state["explicit"])
        session = state["session"]
        active = session.active_vendor_id
        # Only vendors resolve against the vendor catalog; contracts resolve through the master below, and
        # every other entity (organization, application, product ...) is a lookup hint, never a vendor.
        echoed = False
        for mention in [e["mention"] for e in understanding["entities"] if e["type"] == "Vendor"]:
            if REFERENCE.match(mention.strip()):
                echoed = True
                continue
            if not _mentioned(mention, question):
                # The model often echoes the vendor in focus for a follow-up ('are there any application
                # names?'); that is the active entity, not an invented one. Any other unmentioned vendor is rejected.
                if active and self._same_vendor(mention, active):
                    echoed = True
                    continue
                raise AgentError("Model entity mention was not present in the question", reason="mention_not_in_question",
                                 detail=self._vendor_label(mention))
            ids.add(agent.resolver.resolve(mention))
        # A follow-up ('this contract', 'its', or an elliptical question about the same vendor) falls back to
        # the session's focus when nothing new is named; a new vendor or contract always overrides history.
        follow_up = not ids and bool(active) and (understanding["scope"] == "focus" or echoed or bool(FOLLOW_UP.search(question)))
        if follow_up:
            ids.add(agent.resolver.resolve(active))
        ids = sorted(ids)
        contracts = agent.resolver.contracts(question)
        if not contracts and follow_up and session.active_contract_id:
            contracts = [session.active_contract_id]
        result["resolved_entities"] = {"vendor_ids": ids, "contract_ids": contracts,
                                       "resolution": "session follow-up" if follow_up else "canonical exact match (vendor or contract ID)",
                                       "references": [e for e in understanding["entities"] if e["type"] not in ("Vendor", "Contract")]}
        names = result["route"]["specialists"]
        if not names or len(names) != len(set(names)):
            raise AgentError("Invalid specialist selection", reason="specialists_declined", detail="No specialist")
        notes, explore_only = [], []
        vendor_scoped = [n for n in names if n in VENDOR_SCOPED]
        if not ids and vendor_scoped:
            if agent.explorer is not None:
                # No certified tool covers the portfolio, so these specialists answer from guarded
                # read-only queries across all vendors instead of stopping the question.
                explore_only = vendor_scoped
                notes.append({"code": "portfolio_exploration", "message": f"{', '.join(vendor_scoped)} has no certified portfolio "
                              "tool; its context comes from read-only source queries across all vendors."})
            elif len(vendor_scoped) == len(names):
                return {"stop": _stop("vendor_required"), "trace_detail": {"vendor_ids": ids, "specialists": names}}
            else:
                # A portfolio question keeps its portfolio specialists; vendor-only context is skipped and said so.
                names = [n for n in names if n not in VENDOR_SCOPED]
                notes.append({"code": "specialist_skipped", "message": f"{', '.join(vendor_scoped)} context needs a named vendor "
                              "and was not run for this portfolio question."})
        return {"ids": ids, "contracts": contracts, "specialists": names, "explore_only": explore_only, "notes": notes,
                "trace_detail": {"vendor_ids": ids, "contract_ids": contracts, "specialists": names,
                                 **({"follow_up": True} if follow_up else {}), **({"explore_only": explore_only} if explore_only else {})}}

    def _session_context(self, question, session):
        """'Which vendor are we talking about?': answered from the session and the canonical catalog.
        Used only after the router declined the question, so it never overrides a business route."""
        if not CONTEXT_QUESTION.search(question):
            return None
        active = session.active_vendor_id
        name = next((v["vendor_name"] for v in self.agent.resolver.catalog if v["vendor_id"] == active), None)
        if not active or not name:
            return {"status": "clarification", "code": "session_context", "message": "No vendor is in context yet. "
                    "Name one (for example V-001 or its exact name) and follow-up questions will refer to it."}
        contract = f", contract {session.active_contract_id}" if session.active_contract_id else ""
        return {"status": "answered", "code": "session_context", "message": f"We are discussing {active} ({name}){contract}, from "
                "your earlier question. Follow-ups such as 'this vendor', 'this contract' or 'its' refer to it until you name another "
                "vendor or contract."}

    def _remember(self, state, result, code=None):
        """Update the session's focus and its short memory after every turn, answered or not (code: the stop, if any)."""
        session = state["session"]
        ids, contracts = state.get("ids") or [], state.get("contracts") or []
        if code == "vendor_resolution":
            session.active_vendor_id = session.active_contract_id = None
        elif code is None and ids:
            # One vendor becomes the focus; several (a comparison) clear it. A portfolio question keeps the focus.
            vendor = ids[0] if len(ids) == 1 else None
            owned = [c for c in contracts if self.agent.resolver.contract_vendor.get(c) == vendor]
            session.active_vendor_id = vendor
            session.active_contract_id = (owned[0] if len(owned) == 1 else self.agent.resolver.contract_of(vendor)) if vendor else None
        if code == "session_context":
            return
        narrative = result.get("narrative") or {}
        session.remember(state.get("question") or "", result["status"], ids, contracts,
                         narrative.get("summary") or (result.get("final_answer") or "").split("\n")[0])

    def _same_vendor(self, mention, vendor_id):
        try:
            return self.agent.resolver.resolve(mention) == vendor_id
        except Exception:
            return False

    def dispatch_plans(self, state):
        if state.get("stop"):
            return "stop"
        route, skip = state["result"]["route"], set(state.get("explore_only") or ())
        interpreted = state["understanding"]["standalone_question"]
        sends = [Send(f"plan_{name}", {"question": state["question"], "interpreted": interpreted, "ids": state["ids"], "focus": route["focus"],
                               "name": name, "index": index}) for index, name in enumerate(state["specialists"]) if name not in skip]
        return sends or "validate"

    def plan(self, task):
        agent, name = self.agent, task["name"]
        try:
            calls = agent.specialists[name].plan(task["question"], task["ids"], task["focus"], interpreted=task.get("interpreted"))
            # Parameters are checked against the user's own words, never the model's rewrite.
            prepared = [agent.tools.prepare(name, call, task["question"], task["ids"]) for call in calls]
            entry, detail = (task["index"], prepared, None), {"tools": [c["name"] for c in prepared]}
        except SpecialistDeclined:
            entry, detail = (task["index"], None, {"declined": name}), {"status": "declined"}
        except Exception as exc:
            entry, detail = (task["index"], None, _failure(exc)), {"status": "rejected"}
        return {"plans": [entry], "trace_detail": {"agent": name, **detail}}

    def validate(self, state):
        agent = self.agent
        plans = sorted(state.get("plans", []), key=lambda p: p[0])
        # An invalid call fails the whole plan; a specialist that declines is dropped only
        # when another specialist has a valid plan, and the answer says so.
        for _, _, failure in plans:
            if failure and "declined" not in failure:
                return {"stop": failure}
        declined = [failure["declined"] for _, _, failure in plans if failure]
        if len(declined) == len(plans) and not state.get("explore_only"):
            return {"stop": _stop("invalid_plan", reason="specialists_declined", detail=_names(declined))}
        notes = list(state.get("notes") or [])
        if declined:
            notes.append({"code": "specialist_declined", "message": f"{', '.join(declined)} found nothing to look up for this "
                          "question and was not run."})
        # All plans are validated together before any service call runs.
        prepared = [call for _, calls, _ in plans if calls for call in calls]
        ids = state["ids"]
        if len(prepared) > agent.max_tool_calls or sum(agent.tools.cost(c) for c in prepared) > agent.max_service_calls:
            raise AgentError("Request exceeds the tool-call bound", reason="too_broad")
        if ids and {c["arguments"].get("vendor_id") for c in prepared} != set(ids):
            raise AgentError("Planned tools do not cover all resolved vendors", reason="plan_incomplete")
        signatures = [json.dumps(c, sort_keys=True) for c in prepared]
        if len(signatures) != len(set(signatures)):
            raise AgentError("Duplicate tool calls are not permitted")
        return {"prepared": prepared, "notes": notes, "specialists": [n for n in state["specialists"] if n not in declined],
                "trace_detail": {"tool_calls": len(prepared), **({"declined": declined} if declined else {})}}

    def execute(self, state):
        result, trace = state["result"], []
        for call in state["prepared"]:
            started = time.perf_counter()
            tool = self.agent.tools.execute(call)
            tool["tool_id"] = f"T{len(result['tool_results']) + 1}"
            result["tool_results"].append(tool)
            if call["specialist"] not in result["specialists_used"]:
                result["specialists_used"].append(call["specialist"])
            trace.append({"node": "execute.tool", "agent": call["specialist"], "action": call["name"], "tool_id": tool["tool_id"],
                          "arguments": deepcopy(tool["arguments"]), "status": "ok" if tool["result"]["facts"] else "empty",
                          "evidence": len(tool["result"]["evidence"]), "duration_ms": round((time.perf_counter() - started) * 1000)})
        return {"trace": trace}

    # Exploration ------------------------------------------------------------

    def _task(self, state, name, index, round_, gaps=()):
        result, agent = state["result"], self.agent
        tools = [t for t in result["tool_results"] if t["specialist"] == name and t["tool"] not in ("run_sql", "run_cypher")]
        certified = [c["text"] for c in build_grounding(tools)["facts"]] if tools else []
        found = re.findall(r"\bCTR-\d{3}\b", json.dumps([t["result"]["facts"] for t in tools], default=str))
        # Contracts the user named (or the session's contract on a follow-up) come first.
        contracts = list(dict.fromkeys([*(state.get("contracts") or []), *sorted(set(found))]))
        names = state["specialists"]
        understanding = state["understanding"]
        requested = self._lookups(understanding)
        return {"task_id": f"{name}-r{round_}", "specialist": name, "index": index, "round": round_,
                # Explorers read the standalone question, so a follow-up keeps its subject.
                "question": understanding["standalone_question"], "objective": SPECIALIST_OBJECTIVES[name], "vendor_ids": state["ids"],
                "scope": "vendor" if state["ids"] else "portfolio",
                # Date arithmetic is anchored on the data snapshot, never the wall clock.
                "as_of_date": agent.tools.decision.structured.as_of_date.isoformat(),
                "contract_ids": contracts[:20], "required_concepts": list(SPECIALIST_CONCEPTS[name]), "gaps": list(gaps),
                "requested": [r["concept"] for r in requested],
                "paths": [p for p in (agent.digest.path("Vendor", r["type"]) for r in requested) if p and p != "Vendor"],
                "anchors": [e["mention"] for e in understanding["entities"] if e["type"] not in ("Vendor", "Contract")][:10],
                "certified": certified, "query_budget": max(1, agent.max_explore_queries // len(names))}

    @staticmethod
    def _lookups(understanding):
        """Requested data a source can hold: declared in the data model and not restricted."""
        return [r for r in understanding["requested"] if r["recorded"] and not r["restricted"]]

    def dispatch_explorers(self, state):
        if state.get("stop"):
            return "stop"
        if self.agent.explorer is None:
            return "diagnose"
        names = state["specialists"]
        return [Send(f"explore_{name}", {"task": self._task(state, name, index, 0)}) for index, name in enumerate(names)]

    def verify(self, state):
        result = state["result"]
        covered = {}
        for tool in result["tool_results"]:
            facts = tool["result"]["facts"]
            if facts and any(v not in (None, "", [], {}) for v in facts.values()):
                covered.setdefault(tool["specialist"], set()).update(SPECIALIST_CONCEPTS[tool["specialist"]])
        for findings in state.get("findings", []):
            covered.setdefault(findings["specialist"], set()).update(findings["concepts_found"])
        gaps = {name: [c for c in SPECIALIST_CONCEPTS[name] if c not in covered.get(name, set())]
                for name in state["specialists"]}
        # What the user asked for is a gap until some row holds it; the first specialist looks again.
        asked = self._missing_requested(state)
        if asked:
            first = state["specialists"][0]
            gaps[first] = list(dict.fromkeys([*gaps.get(first, []), *asked]))
        gaps = {name: missing for name, missing in gaps.items() if missing}
        return {"gaps": gaps, "trace_detail": {"gaps": gaps}}

    def dispatch_gaps(self, state):
        rounds = max((f["round"] for f in state.get("findings", [])), default=0)
        if not state.get("gaps") or rounds >= 1 or not self._anything_found(state):
            # Nothing found at all is clarified before any deeper search.
            return "diagnose"
        names = state["specialists"]
        return [Send(f"explore_{name}", {"task": self._task(state, name, names.index(name), 1, missing)})
                for name, missing in state["gaps"].items()]

    @staticmethod
    def _anything_found(state):
        return bool(state["result"]["tool_results"]) or any(f["observations"] for f in state.get("findings", []))

    def _missing_requested(self, state):
        """Requested attributes (e.g. Application.name) that no certified fact or kept source row holds."""
        understanding = state.get("understanding") or {}
        wanted = [r for r in self._lookups(understanding) if r["attribute"]] if understanding else []
        if not wanted:
            return []
        concepts, columns = set(), set()
        for findings in state.get("findings", []):
            concepts.update(findings["concepts_found"])
            for o in findings["observations"]:
                concepts.update(o.get("concepts", ()))
                columns.update(str(c).split(".")[-1].casefold() for c in o.get("columns", ()))
        keys = set()
        for tool in state["result"]["tool_results"]:
            if tool["tool"] not in ("run_sql", "run_cypher"):
                _keys(tool["result"]["facts"], keys)
        missing = []
        for r in wanted:
            names = self._field_names(r)
            held = any(c.split(".")[0] == r["type"] and c.split(".")[-1].casefold() in names | {r["attribute"].casefold()}
                       for c in concepts)
            # A column or fact key only counts when its name says whose attribute it is (application_name, not name).
            qualified = names - GENERIC_FIELDS
            if not (held or qualified & columns or qualified & keys):
                missing.append(r["concept"])
        return missing

    def _field_names(self, requested):
        """Source field names that would carry a requested attribute: 'name', 'application_name', 'Application_Name' ..."""
        kind, attribute = requested["type"], requested["attribute"].casefold()
        label = self.agent.digest.label(kind).casefold()
        owners = {re.sub(r"(?<!^)(?=[A-Z])", "_", kind).casefold(), kind.casefold(), label.split()[0], label.replace(" ", "_")}
        attributes = {attribute} | ({"name"} if attribute.endswith("name") else set())
        return {f"{o}_{a}" for o in owners for a in attributes} | attributes

    def diagnose(self, state):
        """Compare what was asked for with the data model and with what came back.

        Data the model does not record is said plainly, never inferred; requested data that exists but
        was not returned is named with where the data model keeps it; when nothing came back at all
        the question stops for clarification (with the reason) instead of searching deeper."""
        result, understanding, digest = state["result"], state["understanding"], self.agent.digest
        notes = []
        for r in understanding["requested"]:
            if r["recorded"] is False:
                kind = digest.label(r["type"]).lower()
                held = [p for p, d in digest.registry.properties_for_class(r["type"]).items() if not d.pii]
                notes.append({"code": "not_recorded", "message": f"The data model records no '{r['attribute']}' for a {kind}, so no source "
                              f"holds it and nothing is inferred. What is recorded for a {kind}: {', '.join(held)}."})
            elif r["restricted"]:
                notes.append({"code": "restricted", "message": f"{digest.label(r['type'])} {r['attribute']} is restricted personal data "
                              "and is never returned."})
        missing = self._missing_requested(state)
        if not self._anything_found(state):
            return {"stop": _stop("no_evidence", reason="no_rows"), "notes": [*(state.get("notes") or []), *notes],
                    "trace_detail": {"reason": "no_rows", "missing": missing}}
        scope = ", ".join(state.get("ids") or []) or "the portfolio"
        for concept in missing:
            r = next(x for x in understanding["requested"] if x["concept"] == concept)
            path = digest.path("Vendor", r["type"])
            where = f" In the data model, {path}." if path and path != "Vendor" else ""
            notes.append({"code": "requested_not_found", "message": f"No certified fact or source row returned the {r['attribute'].replace('_', ' ')} "
                          f"of the {digest.label(r['type']).lower()} for {scope}; nothing is inferred.{where}"})
        understanding["notes"] = [n["message"] for n in notes]
        result["understanding"] = understanding
        return {"notes": [*(state.get("notes") or []), *notes], "understanding": understanding,
                "trace_detail": {"missing": missing, "notes": [n["code"] for n in notes]}}

    # Answer -------------------------------------------------------------------

    def ground(self, state):
        result, agent = state["result"], self.agent
        extra, seen = [], {json.dumps([t["tool"], t["arguments"]], sort_keys=True) for t in result["tool_results"]}
        for findings in sorted(state.get("findings", []), key=lambda f: (f["round"], f["index"])):
            result["findings"].append({k: findings[k] for k in ("task_id", "specialist", "round", "status", "concepts_found",
                                                                  "links_followed", "queries_run")}
                                      | {"kept": [o["id"] for o in findings["observations"]]})
            extra.extend(findings["notes"])
            for observation in findings["observations"]:
                tool = self._exploration_tool(observation, findings["specialist"])
                signature = json.dumps([tool["tool"], tool["arguments"]["query"]], sort_keys=True)
                if signature in seen:
                    continue
                seen.add(signature)
                tool["tool_id"] = f"T{len(result['tool_results']) + 1}"
                result["tool_results"].append(tool)
                if findings["specialist"] not in result["specialists_used"]:
                    result["specialists_used"].append(findings["specialist"])
        result.update(build_grounding(result["tool_results"]))
        extra[:0] = state.get("notes") or []
        assumption = (state.get("understanding") or {}).get("assumption")
        if assumption:
            extra.insert(0, {"code": "assumption", "message": f"Interpretation: {assumption}"})
        extra.extend(self._contradictions(result["tool_results"]))
        for name, missing in (state.get("gaps") or {}).items():
            extra.append({"code": "coverage_gap", "specialist": name,
                          "message": f"{name}: no certified or source rows were found for {', '.join(missing)}; nothing is inferred."})
        for item in extra:
            if item not in result["limitations"]:
                result["limitations"].append(item)
        result["sources_used"] = self._sources(result["tool_results"])
        if not result["tool_results"]:
            # A portfolio exploration that found no rows has nothing to cite, so nothing is synthesized.
            return {"stop": _stop("no_evidence")}
        return {"trace_detail":{"tool_results": len(result["tool_results"]), "facts": len(result["facts"]),
                                 "sources": [s["source"] for s in result["sources_used"]]}}

    def _exploration_tool(self, o, specialist):
        columns, rows, datasets = o["columns"], o["rows"], o.get("datasets") or []
        # One evidence entry per query: exactly what ran, where, and what it returned.
        evidence = {"query_id": o["id"], "specialist": specialist, "purpose": o["purpose"], "query": o["query"],
                    "columns": columns, "rows": rows, "row_count": o["row_count"], "truncated": o["truncated"]}
        if o["action"] == "run_sql":
            namespace, label = NAMESPACE, ", ".join(datasets)
            evidence = {"source_type": "postgres", "query_language": "sql", "source_dataset": label, "datasets": datasets,
                        "role": "citi_reader (read-only)", **evidence, "records": []}
            # A line number identifies a record only when the query reads a single dataset.
            if "_source_line" in columns and len(datasets) == 1:
                line = columns.index("_source_line")
                evidence["records"] = [f"{label}:line {row[line]}" for row in rows]
        else:
            namespace = CATALOG_NAMESPACE if o.get("graph") == "catalog" else NAMESPACE
            label = f"{o.get('graph', 'business')} graph"
            evidence = {"source_type": "neo4j", "query_language": "cypher", "graph": o.get("graph", "business"),
                        "namespace": namespace, "labels": o.get("labels", []), **evidence}
        evidence = [evidence]
        limitations = [{"code": "exploration_supplementary",
                        "message": "Rows from dynamic queries are supplementary source context; certified tool values take precedence."}]
        if self._unreviewed(o):
            limitations.append({"code": "unreviewed_link", "message": "Some joins used inferred foreign keys that are not yet reviewed."})
        arguments = {"query": o["query"], "purpose": o["purpose"], **({"graph": o["graph"]} if o.get("graph") else {})}
        return {"tool": o["action"], "specialist": specialist, "arguments": arguments, "result": {
            "namespace": namespace, "capability": "exploration", "scope": {"specialist": specialist, "query_id": o["id"]},
            "facts": {"query_id": o["id"], "source": label, "datasets": datasets, "columns": columns, "rows": rows,
                      "row_count": o["row_count"], "truncated": o["truncated"], "source_columns": o.get("source_columns", {})},
            "evidence": evidence, "limitations": limitations, "assumptions": [], "calculations": [], "flags": []}}

    @staticmethod
    def _unreviewed(o):
        datasets = set(o.get("datasets") or ())
        for link in o.get("links", []):
            if any(j["dataset"] in datasets and j["review_state"] != "auto" for j in link["joins_to"]):
                return True
        return False

    def _contradictions(self, tools):
        """Dynamic rows that disagree with a certified master-row value for the same contract."""
        explorer = self.agent.explorer
        home = explorer.index.datasets.get(HOME_DATASET) if explorer else None
        if home is None:
            return []
        concept_of = {f.name: f.concept for f in home.fields if f.concept}
        certified = {}
        for tool in tools:
            facts = tool["result"]["facts"] or {}
            rows = [facts.get("commercial", {}).get("contract")] + [c["facts"].get("commercial", {}).get("contract")
                                                                   for c in facts.get("contracts", []) if c.get("facts")]
            for row in filter(None, rows):
                for column, concept in concept_of.items():
                    if concept != "Contract.contract_id" and row.get(column) not in (None, ""):
                        certified[(concept, row.get("Contract_ID"))] = str(row[column])
        notes = []
        for tool in tools:
            facts = tool["result"]["facts"] or {}
            if tool["tool"] != "run_sql":
                continue
            concepts = {c: explorer.index.concept(d, c) for c, d in facts["source_columns"].items()}
            key = next((c for c, k in concepts.items() if k == "Contract.contract_id"), None)
            if key is None:
                continue
            k = facts["columns"].index(key)
            for row in facts["rows"]:
                for column, concept in concepts.items():
                    expected = certified.get((concept, row[k]))
                    value = row[facts["columns"].index(column)]
                    if expected is not None and value not in (None, "") and str(value).strip() != expected.strip():
                        dataset = facts["source_columns"][column]
                        notes.append({"code": "contradiction", "message": f"Contradiction: {dataset}.{column} is {value} for {row[k]}, "
                                      f"but the certified {concept} is {expected}. The certified value is used."})
        return notes

    @staticmethod
    def _sources(tools):
        sources = {}
        for tool in tools:
            if tool["tool"] in ("run_sql", "run_cypher"):
                names = tool["result"]["facts"]["datasets"] or [tool["result"]["facts"]["source"]]
            else:
                names = [e.get("source_dataset") or e.get("namespace") or e.get("source_type") for e in tool["result"]["evidence"]]
            for name in filter(None, names):
                entry = sources.setdefault(name, {"source": name, "via": [], "specialists": []})
                for key, value in (("via", tool["tool"]), ("specialists", tool["specialist"])):
                    if value not in entry[key]:
                        entry[key].append(value)
        return sorted(sources.values(), key=lambda s: s["source"])

    @staticmethod
    def _required(result, route, grounding):
        """Cards an answer must cover, so a valid but incomplete answer cannot hide listed contracts,
        scenario baselines or material concerns."""
        required = []
        for tool in result["tool_results"]:
            cards = [c for c in grounding["facts"] if c["tool_id"] == tool["tool_id"]]
            topics = set()
            if tool["tool"] == "get_renewal_priorities":
                topics = {"renewal"}
            elif tool["tool"] == "get_renewal_context":
                topics = {"renewal", "risk", "sla"}
            elif tool["tool"] == "get_vendor_360" and route["focus"] == "overview":
                topics = {"overview", "renewal", "spend", "workforce", "dependencies", "risk", "sla"}
            elif tool["tool"] == "get_vendor_dependency_risk":
                topics = {"risk"} if route["focus"] == "risk" else {"dependencies", "workforce", "risk", "sla"}
            elif tool["tool"] == "get_spend_forecast_analysis":
                topics = {"spend"}
            elif tool["tool"] == "run_workforce_scenario":
                topics = {"workforce"} if tool["arguments"]["action"] == "baseline" else {"workforce", "scenario"}
            elif tool["tool"] == "get_vendor_rationalization_opportunities":
                required.append(cards[0]["fact_id"])
            required += [c["fact_id"] for c in cards if c["topic"] in topics]
        return list(dict.fromkeys(required))

    def synthesize(self, state):
        agent, result = self.agent, state["result"]
        route, question = result["route"], state["question"]
        grounding = {k: result[k] for k in ("facts", "calculations", "flags", "evidence", "assumptions", "limitations")}
        cards = {c["fact_id"]: c for c in grounding["facts"]}
        required = self._required(result, route, grounding)
        schema = synthesis_schema(list(cards))
        understanding = state.get("understanding") or {}
        payload = {"prompt_version": prompts.PROMPT_VERSION, "question": question, "focus": route["focus"],
                   "understanding": {"interpreted_question": understanding.get("standalone_question", question),
                                     "requested": [r["concept"] for r in understanding.get("requested", ())],
                                     "not_answered": understanding.get("notes", [])},
                   "required_fact_ids": required, **model_context(grounding)}
        narrative, problems, attempts = None, [], 0
        # The model writes the answer; code checks every value in it against the cited cards,
        # and gives the model one chance to fix what failed before falling back to the cards.
        while narrative is None and attempts < 2:
            attempts += 1
            try:
                candidate = validate(agent.model.complete("supervisor_synthesis", prompts.SYNTHESIS,
                                                          {**payload, **({"previous_problems": problems} if problems else {})}, schema), schema)
            except (AgentError, ModelError):
                break
            problems = verify_narrative(candidate, cards, question)
            if not problems:
                narrative = candidate
        if narrative is not None:
            cited = list(dict.fromkeys(f for p in narrative["paragraphs"] for f in p["fact_ids"]))
            appended = [f for f in required if f not in cited]
            # Every certified tool must be represented; supplementary source rows may be left out.
            represented = {cards[f]["tool_id"] for f in cited + appended}
            for tool in result["tool_results"]:
                first = next((c["fact_id"] for c in grounding["facts"] if c["tool_id"] == tool["tool_id"]), None)
                if first and tool["tool_id"] not in represented and tool["tool"] not in ("run_sql", "run_cypher"):
                    appended.append(first)
                    represented.add(tool["tool_id"])
            selected = cited + appended
            result["narrative"] = narrative
            result["final_answer"] = render_narrative(narrative, result, appended)
        else:
            selected = []
            for tool in result["tool_results"]:
                candidates = [c for c in grounding["facts"] if c["tool_id"] == tool["tool_id"]]
                relevant = [c for c in candidates if c["topic"] == route["focus"]]
                selected.extend(c["fact_id"] for c in (relevant or candidates)[:3])
            selected = list(dict.fromkeys(selected + required))
            result["limitations"].append({"code": "synthesis_fallback", "message": "Model synthesis was unavailable or failed the value checks; "
                                          "source-backed statements are rendered directly."})
            result["final_answer"] = render(selected, result)
        result["selected_fact_ids"] = selected
        result["synthesis_checks"] = {"attempts": attempts, "problems": problems if narrative is None else []}
        result["interpretation"] = [{"basis": "POC decision boundary", "text": "Decision context only; no final renewal, consolidation or staffing recommendation."}]
        self._remember(state, result)
        return {"final_answer": result["final_answer"], "trace_detail": {
            "agent": "supervisor", "selected": len(selected), "fallback": narrative is None, "attempts": attempts,
            **({"problems": problems} if problems and narrative is None else {})}}

    def stop(self, state):
        stop, result = state["stop"], state["result"]
        if stop["code"] in ("route_not_actionable", "invalid_plan") and not result["tool_results"]:
            # "Which vendor are we talking about?" has no business route; answer it from the session instead
            # of a generic stop. Only a question that produced no business answer falls back here.
            stop = self._session_context(state.get("question") or "", state["session"]) or stop
        if stop["code"] == "vendor_resolution":
            result["resolved_entities"] = {"candidates": stop["candidates"]}
        if result["tool_results"]:
            try:
                result.update(build_grounding(result["tool_results"]))
            except Exception:
                pass  # the stop status and message below still apply; returned tool results are kept as-is
        explanation = self._explain(state, stop) if stop["code"] != "session_context" else None
        message = stop["message"] if not explanation else f"{stop['message']} {explanation['text']}"
        result["status"], result["final_answer"] = stop["status"], message
        if explanation:
            understanding = result.get("understanding") or {}
            result["understanding"] = {**understanding, "reason": stop.get("reason") or stop["code"],
                                       "explanation": explanation["why"], "clarifying_question": explanation["question"]}
        if stop["code"] != "session_context":  # that message is the answer itself, not a limitation
            result["limitations"].append({"code": stop["code"], "message": stop["message"]})
            for note in state.get("notes") or []:
                if note not in result["limitations"]:
                    result["limitations"].append(note)
        self._remember(state, result, stop["code"])
        return {"final_answer": message, "trace_detail": {"status": stop["status"], "code": stop["code"],
                                                          **({"reason": stop["reason"]} if stop.get("reason") else {})}}

    def _explain(self, state, stop):
        """What was understood, why it could not proceed, and the one question that would let it proceed.

        Built by the host from the reason code, the understanding and the data model; the model's own
        clarifying question is used only when the model itself declined to route."""
        understanding = state.get("understanding") or {}
        session, digest = state["session"], self.agent.digest
        understood = understanding.get("standalone_question")
        reason, detail = stop.get("reason"), stop.get("detail")
        if stop["code"] == "vendor_resolution" and not reason:
            unknown = [c for c in re.findall(r"\bCTR-\d+\b", state.get("question") or "", re.I)
                       if c.upper() not in self.agent.resolver.contract_vendor]
            reason, detail = ("unknown_contract", ", ".join(unknown)) if unknown else (
                "ambiguous_vendor" if stop["status"] == "clarification" else "unknown_vendor", self._unresolved(state))
        why = REASONS[reason].format(detail=detail or "it") if reason in REASONS else None
        question = None
        if stop["code"] == "route_not_actionable":
            question = understanding.get("clarifying_question")
        elif stop["code"] == "vendor_resolution":
            names = [f"{c['vendor_id']} ({c['vendor_name']})" for c in stop.get("candidates", [])[:5]]
            question = f"Did you mean {', '.join(names[:-1])} or {names[-1]}?" if len(names) > 1 else (
                f"Did you mean {names[0]}?" if names else "Which vendor did you mean? Give its ID (for example V-001) or exact name.")
        elif stop["code"] in ("invalid_plan", "no_evidence", "vendor_required"):
            question = self._what_is_missing(state, understanding, session, digest)
        if stop["code"] in ("model_unavailable", "tool_unavailable", "invalid_question", "unsupported_action"):
            return None
        if not (why or question):
            return None
        lead = f"I understood the question as: \"{understood}\"" if understood else ""
        text = " ".join(part for part in (f"{lead}." if lead else "", why or "", question or "") if part)
        return {"text": text, "why": why, "question": question}

    def _what_is_missing(self, state, understanding, session, digest):
        """One precise question from the data model: which entity, and where the data it asks for lives."""
        requested = [r for r in understanding.get("requested", ()) if r.get("type")]
        anchors = [e for e in understanding.get("entities", ()) if e["type"] not in ("Vendor", "Contract")]
        ids = state.get("ids") or []
        if anchors:
            anchor = anchors[0]
            kind = digest.label(anchor["type"]).lower()
            return (f"I read {anchor['mention']} as {'an' if kind[0] in 'aeiou' else 'a'} {kind}. Is that right, and do you want it on "
                    f"its own or as it relates to {self._vendor_label(ids[0]) if ids else 'a particular vendor'}?")
        target = requested[0] if requested else None
        if target and target["recorded"] is False:
            return None  # the not-recorded note already says what exists instead
        what = (f"the {target['attribute'].replace('_', ' ')} of the {digest.label(target['type']).lower()}s"
                if target and target.get("attribute") else f"the {digest.label(target['type']).lower()}s" if target else "this")
        path = digest.path("Vendor", target["type"]) if target else None
        where = f" (in the data model: {path})" if path and path != "Vendor" else ""
        if ids:
            return f"Do you want {what} for {self._vendor_label(ids[0])}{where}, or for a specific contract or entity? Name it and I will look again."
        if session.active_vendor_id:
            return f"Do you want {what} for {self._vendor_label(session.active_vendor_id)}, the vendor in focus{where}, or across all vendors?"
        return f"Which vendor or contract should I look at for {what}{where}? Give its ID (for example V-001) or exact name."

    def _vendor_label(self, value):
        """'V-001 (Aurelix Codeworks)' for a vendor ID or exact name; the value itself otherwise."""
        value = str(value)
        row = next((v for v in self.agent.resolver.catalog
                    if value.casefold() in (v["vendor_id"].casefold(), v["vendor_name"].casefold())), None)
        return f"{row['vendor_id']} ({row['vendor_name']})" if row else value

    def _unresolved(self, state):
        """The vendor mention that failed to resolve, for the explanation."""
        for entity in (state.get("understanding") or {}).get("entities", ()):
            if entity["type"] == "Vendor":
                try:
                    self.agent.resolver.resolve(entity["mention"])
                except Exception:
                    return entity["mention"]
        found = re.findall(r"\bV-\d+\b", state.get("question") or "", re.I)
        return found[0].upper() if found else None
