"""LangGraph orchestration of the supervisor, specialists, certified tools and explorers.

    guard -> route -> resolve -> plan (one node per specialist, in parallel) -> validate
          -> execute (certified tools) -> explore (one explorer per specialist, in parallel)
          -> verify (re-delegates concept gaps once) -> ground -> synthesize -> render

Early stops go to a single ``stop`` node with the same statuses and messages as the
original sequential supervisor. Without an explorer the explore and verify stages are
skipped, so behaviour is exactly the certified path.
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
from .diagnostics import failure as log_failure, log
from .contracts import (AgentError, ConversationState, ModelError, ROUTE_SCHEMA, SPECIALISTS, SPECIALIST_CONCEPTS, SPECIALIST_OBJECTIVES,
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
    "invalid_plan": ("clarification", "The requested entities or tool parameters could not be validated. Specify the vendor and any scenario percentage/count and target explicitly."),
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
VENDOR_SCOPED = ("vendor360", "risk_dependency", "spend_forecast")


def _stop(code, status=None, **extra):
    default, message = MESSAGES[code]
    return {"status": status or default, "message": message, "code": code, **extra}


def _failure(exc):
    log_failure(exc)
    if isinstance(exc, EntityResolutionError):
        return _stop("vendor_resolution", exc.status, candidates=exc.candidates)
    if isinstance(exc, AgentError):
        return _stop("invalid_plan")
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
        for name, fn in (("guard", self.guard), ("route", self.route), ("resolve", self.resolve), ("validate", self.validate),
                         ("execute", self.execute), ("verify", self.verify), ("ground", self.ground),
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
        g.add_conditional_edges("guard", self._next("route"), ["route", "stop"])
        g.add_conditional_edges("route", self._next("resolve"), ["resolve", "stop"])
        g.add_conditional_edges("resolve", self.dispatch_plans, [*plans, "validate", "stop"])
        g.add_conditional_edges("validate", self._next("execute"), ["execute", "stop"])
        g.add_conditional_edges("execute", self.dispatch_explorers, [*explorers, "ground", "stop"])
        g.add_conditional_edges("verify", self.dispatch_gaps, [*explorers, "ground"])
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

    def route(self, state):
        agent, question, result = self.agent, state["question"], state["result"]
        explicit = agent.resolver.explicit(question)
        route = validate(agent.model.complete("supervisor_route", prompts.SUPERVISOR,
            {"prompt_version": prompts.PROMPT_VERSION, "question": question, "canonical_vendors": agent.resolver.catalog,
             "active_vendor_id": state["session"].active_vendor_id, "conversation": state["session"].context()},
            ROUTE_SCHEMA), ROUTE_SCHEMA)
        # The validated route is kept for diagnosis of routing decisions.
        result["intent"], result["route"] = route["focus"], deepcopy(route)
        log.info("agent_route specialists=%s status=%s", route["specialists"], route["status"])
        detail = {"trace_detail": {"agent": "supervisor", "specialists": route["specialists"], "focus": route["focus"],
                                   "route_status": route["status"]}}
        if route["status"] != "route":
            return {"stop": _stop("route_not_actionable", route["status"]), **detail}
        return {"explicit": explicit, **detail}

    def resolve(self, state):
        agent, question, result = self.agent, state["question"], state["result"]
        route = result["route"]
        ids = set(state["explicit"])
        session = state["session"]
        active = session.active_vendor_id
        for mention in route["entity_mentions"]:
            if not _mentioned(mention, question):
                # The router often echoes the session's vendor for a follow-up ('this vendor'); that is the
                # active entity, not an invented one. Any other unmentioned vendor is still rejected.
                if route["use_active_entity"] and active and self._same_vendor(mention, active):
                    continue
                raise AgentError("Model entity mention was not present in the question")
            ids.add(agent.resolver.resolve(mention))
        # A follow-up ('this contract', 'its') falls back to the session's focus when nothing new is named;
        # a new vendor or contract always overrides history.
        follow_up = not ids and bool(active) and (route["use_active_entity"] or bool(FOLLOW_UP.search(question)))
        if follow_up:
            ids.add(agent.resolver.resolve(active))
        ids = sorted(ids)
        contracts = agent.resolver.contracts(question)
        if not contracts and follow_up and session.active_contract_id:
            contracts = [session.active_contract_id]
        result["resolved_entities"] = {"vendor_ids": ids, "contract_ids": contracts,
                                       "resolution": "session follow-up" if follow_up else "canonical exact match (vendor or contract ID)"}
        names = route["specialists"]
        if not names or len(names) != len(set(names)):
            raise AgentError("Invalid specialist selection")
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
        sends = [Send(f"plan_{name}", {"question": state["question"], "ids": state["ids"], "focus": route["focus"],
                               "name": name, "index": index}) for index, name in enumerate(state["specialists"]) if name not in skip]
        return sends or "validate"

    def plan(self, task):
        agent, name = self.agent, task["name"]
        try:
            calls = agent.specialists[name].plan(task["question"], task["ids"], task["focus"])
            prepared = [agent.tools.prepare(name, call, task["question"], task["ids"]) for call in calls]
            entry, detail = (task["index"], prepared, None), {"tools": [c["name"] for c in prepared]}
        except SpecialistDeclined:
            log.warning("agent_plan specialist=%s category=specialist_declined", name)
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
            log.warning("agent_validation_failure category=all_specialists_declined")
            return {"stop": _stop("invalid_plan")}
        notes = list(state.get("notes") or [])
        if declined:
            notes.append({"code": "specialist_declined", "message": f"{', '.join(declined)} found nothing to look up for this "
                          "question and was not run."})
        # All plans are validated together before any service call runs.
        prepared = [call for _, calls, _ in plans if calls for call in calls]
        ids = state["ids"]
        if len(prepared) > agent.max_tool_calls or sum(agent.tools.cost(c) for c in prepared) > agent.max_service_calls:
            raise AgentError("Request exceeds the tool-call bound")
        if ids and {c["arguments"].get("vendor_id") for c in prepared} != set(ids):
            raise AgentError("Planned tools do not cover all resolved vendors")
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
        return {"task_id": f"{name}-r{round_}", "specialist": name, "index": index, "round": round_,
                "question": state["question"], "objective": SPECIALIST_OBJECTIVES[name], "vendor_ids": state["ids"],
                "scope": "vendor" if state["ids"] else "portfolio",
                # Date arithmetic is anchored on the data snapshot, never the wall clock.
                "as_of_date": agent.tools.decision.structured.as_of_date.isoformat(),
                "contract_ids": contracts[:20], "required_concepts": list(SPECIALIST_CONCEPTS[name]), "gaps": list(gaps),
                "certified": certified, "query_budget": max(1, agent.max_explore_queries // len(names))}

    def dispatch_explorers(self, state):
        if state.get("stop"):
            return "stop"
        if self.agent.explorer is None:
            return "ground"
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
        gaps = {name: missing for name, missing in gaps.items() if missing}
        return {"gaps": gaps, "trace_detail": {"gaps": gaps}}

    def dispatch_gaps(self, state):
        rounds = max((f["round"] for f in state.get("findings", [])), default=0)
        if not state.get("gaps") or rounds >= 1:
            return "ground"
        names = state["specialists"]
        return [Send(f"explore_{name}", {"task": self._task(state, name, names.index(name), 1, missing)})
                for name, missing in state["gaps"].items()]

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
        payload = {"prompt_version": prompts.PROMPT_VERSION, "question": question, "focus": route["focus"],
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
        result["status"], result["final_answer"] = stop["status"], stop["message"]
        if stop["code"] != "session_context":  # that message is the answer itself, not a limitation
            result["limitations"].append({"code": stop["code"], "message": stop["message"]})
        self._remember(state, result, stop["code"])
        return {"final_answer": stop["message"], "trace_detail": {"status": stop["status"], "code": stop["code"]}}
