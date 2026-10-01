"""Explorer sub-agent: catalog discovery, guarded dynamic queries, retries and link following.

A LangGraph sub-graph, run once per specialist task:

    discover -> decide (model) -> act (guard, execute, observe) -> decide ... -> END

The model chooses each step; this code enforces every budget and guard. Query rows are
kept as observations. The orchestrator renders the ones the model keeps as fact cards,
so the model never writes a value into the answer.
"""

from dataclasses import asdict, dataclass, replace
import json
import time
from typing import TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from . import prompts
from .contracts import AgentError, ModelError, obj, validate
from .executors import QueryFailed
from .query_guard import QueryRejected, guard_cypher, guard_sql


@dataclass(frozen=True)
class ExploreBudget:
    queries: int = 6        # executed SQL/Cypher queries per task
    retries: int = 2        # consecutive retries after a failed or empty query
    link_depth: int = 3     # follow_link queries per task
    searches: int = 3       # search_catalog calls per task
    extra_steps: int = 3    # model steps beyond the query budget (searches, finish)
    rows_shown: int = 20    # rows per observation shown to the model

    def __post_init__(self):
        for name, ceiling in (("queries", 10), ("retries", 3), ("link_depth", 5), ("searches", 5), ("extra_steps", 6), ("rows_shown", 50)):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= ceiling:
                raise AgentError("Invalid exploration budget")

    @property
    def steps(self):
        return self.queries + self.extra_steps


def _strings(max_items, max_length=80):
    return {"type": "array", "items": {"type": "string", "maxLength": max_length}, "maxItems": max_items}


STEP_SCHEMA = obj({
    "thought": {"type": "string", "maxLength": 400},
    "action": {"type": "string", "enum": ["search_catalog", "run_sql", "run_cypher", "finish"]},
    "purpose": {"type": "string", "enum": ["initial", "retry", "follow_link", "none"]},
    "concepts": _strings(10),
    "terms": _strings(10),
    "datasets": _strings(4, 120),
    "graph": {"type": ["string", "null"], "enum": ["business", "catalog", None]},
    "query": {"type": ["string", "null"], "maxLength": 4000},
    "keep": {"type": "array", "items": {"type": "string", "pattern": "^Q[0-9]{1,2}$"}, "maxItems": 8},
})


def graph_schema(registry):
    """Compact business-graph schema for Cypher: labels with properties, and relationship shapes."""
    metadata = {c.id for c in registry.classes.values() if c.module == "data"}
    labels = {c.id: [p for p, d in registry.properties_for_class(c.id).items() if not d.pii]
              for c in registry.classes.values() if c.id not in metadata}
    relationships = sorted({f"({s})-[:{r.id}]->({t})" for r in registry.relations.values() for s in r.source for t in r.target
                            if s not in metadata and t not in metadata})
    return {"labels": labels, "relationships": relationships,
            "note": "Nodes also carry their ancestor classes as labels. Property names are exactly as listed."}


def pii_properties(registry):
    return sorted({p for c in registry.classes for p, d in registry.properties_for_class(c).items() if d.pii})


def _clip(value, limit=120):
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
        return text[:limit] + "…" if len(text) > limit else value
    return value


class ExploreInput(TypedDict):
    task: dict


class ExploreOutput(TypedDict):
    # These keys merge into the orchestrator's reducer channels when the explorer runs as its node.
    findings: list
    trace: list


class ExploreState(TypedDict, total=False):
    task: dict
    started: float
    catalog: list
    observations: list
    step: dict
    counters: dict
    done: bool
    step_finished: bool
    keep: list
    notes: list
    steps: list      # this explorer's own step log
    findings: list
    trace: list


def _emit(steps):
    """Send new steps to a streaming caller (LangGraph custom stream); a no-op otherwise."""
    if not steps:
        return
    try:
        writer = get_stream_writer()
    except Exception:
        return
    for entry in steps:
        writer({"explore_step": entry})


def _safe(fn):
    """Exploration is supplementary: an unexpected node failure stops this explorer, never the answer."""
    def run(state):
        before = len(state.get("steps") or ())
        try:
            update = fn(state)
        except Exception:
            note = {"code": "exploration_failed", "message": "Dynamic exploration failed; only certified results are used."}
            return {"done": True, "notes": (state.get("notes") or []) + [note]}
        _emit((update.get("steps") or [])[before:])
        return update
    return run


class Explorer:
    def __init__(self, model, index, *, sql=None, cypher=None, graph_schema=None, pii_properties=(),
                 budget=None, max_payload_chars=50000):
        if sql is None and cypher is None:
            raise AgentError("The explorer needs a SQL or Cypher executor")
        if type(max_payload_chars) is not int or not 5000 <= max_payload_chars <= 110000:
            raise AgentError("Invalid exploration payload bound")
        self.model, self.index, self.sql, self.cypher = model, index, sql, cypher
        self.schema = graph_schema if cypher is not None else None
        self.pii = tuple(pii_properties)
        self.budget = budget or ExploreBudget()
        self.max_payload_chars = max_payload_chars
        graph = StateGraph(ExploreState, input_schema=ExploreInput, output_schema=ExploreOutput)
        graph.add_node("discover", _safe(self._discover))
        graph.add_node("decide", _safe(self._decide))
        graph.add_node("act", _safe(self._act))
        graph.add_node("report", self._report)
        graph.add_edge(START, "discover")
        graph.add_conditional_edges("discover", lambda s: "report" if s.get("done") else "decide", ["decide", "report"])
        graph.add_conditional_edges("decide", lambda s: "report" if s.get("done") else "act", ["act", "report"])
        graph.add_conditional_edges("act", lambda s: "report" if s.get("done") else "decide", ["decide", "report"])
        graph.add_edge("report", END)
        # Each model step is two supersteps (decide, act); the ceiling covers the largest allowed budget.
        self.graph = graph.compile().with_config(recursion_limit=4 * (10 + 6) + 10)

    def budget_for(self, queries):
        return replace(self.budget, queries=max(1, min(self.budget.queries, queries)))

    def run(self, task):
        """Explore one task and return its findings; never raises for model or query failures."""
        return self.graph.invoke({"task": task})["findings"][0]

    # Nodes ---------------------------------------------------------------

    def _discover(self, state):
        budget = self.budget_for(state["task"].get("query_budget", self.budget.queries))
        task = {**state["task"], "budget": asdict(budget)}
        concepts = list(dict.fromkeys([*task.get("required_concepts", ()), *task.get("gaps", ())]))
        catalog = self.index.search(concepts=concepts, terms=task.get("terms", ()), limit=8)
        entry = {"node": "explore.discover", "agent": task["specialist"], "action": "search_catalog",
                 "concepts": concepts, "datasets": [d["dataset"] for d in catalog], "status": "ok"}
        return {"task": task, "started": time.perf_counter(), "catalog": catalog, "observations": [], "notes": [], "steps": [entry],
                "counters": {"steps": 0, "queries": 0, "searches": 0, "failures": 0, "depth": 0}}

    def _report(self, state):
        """Findings for the orchestrator, plus its trace entry with this explorer's steps nested inside."""
        task, observations = state["task"], state.get("observations") or []
        notes, steps = state.get("notes") or [], state.get("steps") or []
        kept = [o for o in observations if o["id"] in set(state.get("keep") or ()) and o["status"] == "ok"]
        if not state.get("keep") and not state.get("step_finished"):
            # Budget or model stop: keep every successful query so its rows are not lost.
            kept = [o for o in observations if o["status"] == "ok" and o["action"] != "search_catalog"]
        queries = (state.get("counters") or {}).get("queries", 0)
        duration = round((time.perf_counter() - state["started"]) * 1000) if state.get("started") else 0
        findings = {"task_id": task["task_id"], "specialist": task["specialist"], "round": task.get("round", 0),
                    "index": task.get("index", 0), "status": "failed" if notes and not kept else "complete",
                    "observations": kept, "concepts_found": sorted({c for o in kept for c in o.get("concepts", ())}),
                    "links_followed": [o["link"] for o in observations if o.get("link")],
                    "queries_run": queries, "notes": notes, "trace": steps, "duration_ms": duration}
        entry = {"node": "explore", "status": "ok", "duration_ms": duration, "agent": task["specialist"], "round": findings["round"],
                 "outcome": findings["status"], "queries": queries, "kept": [o["id"] for o in kept], "steps": steps}
        return {"findings": [findings], "trace": [entry]}

    def _payload(self, state):
        task, counters, budget = state["task"], state["counters"], state["task"]["budget"]
        views = [dict(o["view"]) for o in state["observations"]]
        payload = {
            "prompt_version": prompts.EXPLORER_PROMPT_VERSION, "specialist": task["specialist"], "question": task["question"],
            "task": {k: task.get(k) for k in ("objective", "scope", "as_of_date", "vendor_ids","contract_ids", "required_concepts", "gaps")},
            "certified_findings": task.get("certified", [])[:12],
            "catalog": state["catalog"], "business_graph": self.schema,
            "available_actions": [a for a, ok in (("search_catalog", True), ("run_sql", self.sql is not None),
                                                  ("run_cypher", self.cypher is not None), ("finish", True)) if ok],
            "observations": views,
            "budget_left": {"queries": budget["queries"] - counters["queries"], "steps": budget["queries"] + budget["extra_steps"] - counters["steps"],
                            "searches": budget["searches"] - counters["searches"], "retries": max(0, budget["retries"] - counters["failures"]),
                            "follow_links": budget["link_depth"] - counters["depth"]},
        }
        # Older observations lose their rows first, then the catalog shrinks, to stay within the model's bound.
        room = self.max_payload_chars - len(prompts.EXPLORER) - len(json.dumps(STEP_SCHEMA))
        for view in views[:-3]:
            view.pop("rows", None)
            view.pop("results", None)
        while len(json.dumps(payload, ensure_ascii=False, default=str)) > room:
            trimmed = next((v for v in views if v.get("rows") or v.get("results")), None)
            if trimmed is not None:
                trimmed.pop("rows", None)
                trimmed.pop("results", None)
            elif payload["catalog"] and any("fields" in d for d in payload["catalog"]):
                payload["catalog"] = [{k: v for k, v in d.items() if k != "fields"} for d in payload["catalog"]]
            elif payload["certified_findings"]:
                payload["certified_findings"] = []
            else:
                raise AgentError("Exploration context exceeds the model bound")
        return payload

    def _decide(self, state):
        counters = dict(state["counters"])
        task = state["task"]
        if counters["steps"] >= task["budget"]["queries"] + task["budget"]["extra_steps"]:
            return {"done": True, "steps": state["steps"] + [{"node": "explore.decide", "agent": task["specialist"],
                                                              "action": "stop", "status": "step_budget"}]}
        counters["steps"] += 1
        started = time.perf_counter()
        try:
            step = validate(self.model.complete(f"explore_{task['specialist']}", prompts.EXPLORER, self._payload(state), STEP_SCHEMA), STEP_SCHEMA)
        except (AgentError, ModelError) as exc:
            code = "exploration_model_unavailable" if isinstance(exc, ModelError) else "exploration_invalid_step"
            note = {"code": code, "message": "Dynamic exploration stopped: the model was unavailable or returned an invalid step."}
            return {"done": True, "counters": counters, "notes": state["notes"] + [note],
                    "steps": state["steps"] + [{"node": "explore.decide", "agent": task["specialist"], "action": "stop",
                                                "status": code, "duration_ms": round((time.perf_counter() - started) * 1000)}]}
        return {"step": step, "counters": counters}

    def _observe(self, state, observation, entry, counters, **extra):
        view = {k: v for k, v in observation.items() if k in ("id", "action", "purpose", "graph", "query", "status", "message",
                                                                "datasets", "columns", "row_count", "truncated", "links", "results")}
        if "rows" in observation:
            view["rows"] = [[_clip(v) for v in row] for row in observation["rows"][:state["task"]["budget"]["rows_shown"]]]
        observation["view"] = view
        entry.update(status=observation["status"], **{k: observation[k] for k in ("row_count", "message") if k in observation})
        return {"observations": state["observations"] + [observation], "steps": state["steps"] + [entry], "counters": counters, **extra}

    def _act(self, state):
        step, task = state["step"], state["task"]
        budget, counters = task["budget"], dict(state["counters"])
        action, purpose = step["action"], step["purpose"]
        started = time.perf_counter()
        entry = {"node": "explore.act", "agent": task["specialist"], "step": counters["steps"], "action": action, "purpose": purpose,
                 "thought": step["thought"][:200]}
        if action == "finish":
            entry.update(status="ok", keep=list(step["keep"]))
            return {"done": True, "keep": list(step["keep"]), "step_finished": True, "steps": state["steps"] + [entry]}
        n = sum(o["action"] != "search_catalog" for o in state["observations"]) + 1
        if action == "search_catalog":
            observation = {"id": f"C{counters['searches'] + 1}", "action": action, "purpose": purpose}
            if counters["searches"] >= budget["searches"]:
                observation.update(status="refused", message="Search budget used; query the datasets already found or finish.")
            else:
                counters["searches"] += 1
                observation.update(status="ok", results=self.index.search(step["concepts"], step["terms"], step["datasets"], limit=6))
            entry["duration_ms"] = round((time.perf_counter() - started) * 1000)
            return self._observe(state, observation, entry, counters)

        observation = {"id": f"Q{n}", "action": action, "purpose": purpose, "query": step["query"]}
        # Every attempt is traceable, including refused and rejected ones (the guard replaces it on success).
        entry.update(query_id=observation["id"], query=(step["query"] or "")[:1000],
                     **({"graph": step["graph"] or "business"} if action == "run_cypher" else {}))
        refusal = None
        if (self.sql if action == "run_sql" else self.cypher) is None:
            refusal = f"{action} is not available in this deployment."
        elif counters["queries"] >= budget["queries"]:
            refusal = "Query budget used; finish with the observations you have."
        elif purpose == "retry" and counters["failures"] > budget["retries"]:
            refusal = "Retry budget used for this line of inquiry; try a different dataset or finish."
        elif purpose == "follow_link" and counters["depth"] >= budget["link_depth"]:
            refusal = "Link depth budget used; finish with the observations you have."
        if refusal:
            observation.update(status="refused", message=refusal)
            return self._observe(state, observation, entry, counters)
        if purpose == "initial":
            counters["failures"] = 0
        try:
            if action == "run_sql":
                guarded = guard_sql(step["query"], self.index, max_rows=self.sql.max_rows)
                observation.update(query=guarded.sql, datasets=list(guarded.datasets))
            else:
                guarded = guard_cypher(step["query"], graph=step["graph"] or "business", pii_properties=self.pii)
                observation.update(query=guarded.cypher, graph=guarded.graph, labels=list(guarded.labels))
        except QueryRejected as exc:
            counters["failures"] += 1
            observation.update(status="rejected", message=str(exc))
            entry["duration_ms"] = round((time.perf_counter() - started) * 1000)
            return self._observe(state, observation, entry, counters)
        counters["queries"] += 1
        if purpose == "follow_link":
            counters["depth"] += 1
            observation["link"] = {"step": observation["id"], "datasets": observation.get("datasets") or [observation.get("graph")]}
        entry.update(query=observation["query"][:1000], datasets=observation.get("datasets"), graph=observation.get("graph"))
        try:
            result = (self.sql if action == "run_sql" else self.cypher).run(guarded)
        except QueryFailed as exc:
            counters["failures"] += 1
            observation.update(status="error", message=str(exc))
        else:
            rows = result["rows"]
            observation.update(status="ok" if rows else "empty", columns=result["columns"], rows=rows, row_count=len(rows),
                               truncated=result["truncated"])
            if rows:
                counters["failures"] = 0
                observation.update(self._annotate(guarded, result, action))
            else:
                counters["failures"] += 1
                observation["message"] = "No rows matched; check filters, value formats and join keys, or try a linked dataset."
        entry["duration_ms"] = round((time.perf_counter() - started) * 1000)
        return self._observe(state, observation, entry, counters)

    def _annotate(self, guarded, result, action):
        """Concepts found in the result columns, and catalog links from key columns with their values."""
        columns = result["columns"]
        if action == "run_cypher":
            labels = set(guarded.labels)
            return {"concepts": sorted(labels | {f"{label}.{c.split('.')[-1]}" for label in labels for c in columns})}
        sources = {}
        for dataset, column in guarded.columns:
            if column in columns:
                sources.setdefault(column, dataset)
        concepts, links = set(), []
        for column, dataset in sources.items():
            concept = self.index.concept(dataset, column)
            if concept:
                concepts.add(concept)
            joined = self.index.links(dataset, column)
            if joined:
                position = columns.index(column)
                values = list(dict.fromkeys(row[position] for row in result["rows"] if row[position] not in (None, "")))[:5]
                links.append({"column": column, "values": values,
                              "joins_to": [{k: j[k] for k in ("dataset", "field", "review_state")} for j in joined[:8]]})
        return {"concepts": sorted(concepts), "links": links, "source_columns": sources}
