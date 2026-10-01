"""Explorer sub-agent and exploring orchestrator: retries, links, budgets, isolation, re-delegation, contradictions."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from agent_fakes import DEMOS, call, route
from explore_fakes import ExplorerModel, FixedExecutor, SqliteExecutor, schema_index, step
from test_agent_routing import decision
from citi_project.services.agents import ConversationState, ModelError, SupervisorAgent
from citi_project.services.agents.executors import CypherExecutor, QueryFailed, SqlExecutor
from citi_project.services.agents.explorer import ExploreBudget, Explorer, graph_schema, pii_properties
from citi_project.services.agents.query_guard import guard_cypher, guard_sql
from citi_project.services.ontology import OntologyRegistry

MASTER = 'SELECT "Contract_ID", "Contract_End_Date", _source_line FROM clm.canonical_vendor_master WHERE "Vendor_ID" = \'V-001\''
FORECAST = ('SELECT "Contract_Number", "Col_2026_YTP", _source_line FROM finance.ct_vendor_technology_forecast '
            "WHERE \"Contract_Number\" = 'CTR-001'")
WORKFORCE = ('SELECT "Assignment_ID", "WORK_COUNTRY", "WORKER_TYPE", _source_line FROM workforce.ct_workforce_organization '
             "WHERE \"Contract_ID\" = 'CTR-001'")


def task(specialist="vendor360", **extra):
    return {"task_id": f"{specialist}-r0", "specialist": specialist, "index": 0, "round": 0, "question": "What do we know about V-001?",
            "objective": "Build the picture.", "vendor_ids": ["V-001"], "contract_ids": ["CTR-001"],
            "required_concepts": ["Contract.end_date"], "gaps": [], "certified": ["V-001 is Aurelix Codeworks."], **extra}


def explorer(model, sql=None, cypher=None, **kwargs):
    return Explorer(model, schema_index(), sql=sql if sql is not None else SqliteExecutor(), cypher=cypher, **kwargs)


def model(steps):
    return ExplorerModel(route(["vendor360"], "V-001"), [], {"vendor360": steps})


# ------------------------------------------------------------------ explorer

def test_explorer_retries_after_rejection_and_follows_links():
    m = model([step("run_sql", 'SELECT "Contract_End" FROM clm.canonical_vendor_master'),
               step("run_sql", MASTER, purpose="retry"),
               step("run_sql", FORECAST, purpose="follow_link"),
               step("run_sql", WORKFORCE, purpose="follow_link"),
               step("finish", keep=["Q2", "Q3", "Q4"])])
    findings = explorer(m).run(task())
    assert [o["id"] for o in findings["observations"]] == ["Q2", "Q3", "Q4"]
    assert findings["observations"][0]["rows"] == [["CTR-001", "2026-12-27", "2"]]
    assert findings["observations"][1]["rows"][0][:2] == ["CTR-001", "3329863.01"]
    assert len(findings["observations"][2]["rows"]) == 4  # the protected four V-001 assignments
    assert findings["queries_run"] == 3 and len(findings["links_followed"]) == 2
    assert "Contract.end_date" in findings["concepts_found"]
    statuses = [(e["action"], e["status"]) for e in findings["trace"] if e["node"] == "explore.act"]
    assert statuses == [("run_sql", "rejected"), ("run_sql", "ok"), ("run_sql", "ok"), ("run_sql", "ok"), ("finish", "ok")]
    # The model saw the rejection reason and, after Q2, the catalog links with the key values to follow.
    second = m.requests[1][1]["observations"][0]
    assert second["status"] == "rejected" and "Unknown column Contract_End" in second["message"]
    links = m.requests[2][1]["observations"][1]["links"]
    joins = {(j["dataset"], j["field"]) for l in links if l["column"] == "Contract_ID" for j in l["joins_to"]}
    assert ("finance.ct_vendor_technology_forecast", "Contract_Number") in joins
    assert next(l for l in links if l["column"] == "Contract_ID")["values"] == ["CTR-001"]


def test_explorer_retry_budget_after_empty_results():
    empty = 'SELECT "Contract_ID" FROM clm.canonical_vendor_master WHERE "Vendor_ID" = \'V-999\''
    m = model([step("run_sql", empty), step("run_sql", empty, purpose="retry"), step("run_sql", empty, purpose="retry"),
               step("run_sql", MASTER, purpose="retry"), step("finish", keep=["Q1"])])
    findings = explorer(m).run(task())
    acts = [e["status"] for e in findings["trace"] if e["node"] == "explore.act"]
    assert acts == ["empty", "empty", "empty", "refused", "ok"]
    assert findings["observations"] == [] and findings["queries_run"] == 3
    assert "Retry budget used" in m.requests[4][1]["observations"][3]["message"]


def test_explorer_link_depth_and_query_budgets():
    m = model([step("run_sql", MASTER)] + [step("run_sql", FORECAST, purpose="follow_link")] * 3 + [step("finish", keep=["Q1"])])
    findings = explorer(m, budget=ExploreBudget(link_depth=2)).run(task())
    assert [e["status"] for e in findings["trace"] if e["node"] == "explore.act"] == ["ok", "ok", "ok", "refused", "ok"]
    m = model([step("run_sql", MASTER)] * 6)
    findings = explorer(m).run(task(query_budget=2))
    acts = [e for e in findings["trace"] if e["node"].startswith("explore.")]
    assert [e["status"] for e in acts if e["node"] == "explore.act"] == ["ok", "ok", "refused", "refused", "refused"]
    assert acts[-1]["status"] == "step_budget"
    # Without a finish step every successful query is kept, so its rows are not lost.
    assert [o["id"] for o in findings["observations"]] == ["Q1", "Q2"] and findings["queries_run"] == 2


def test_explorer_isolates_model_and_query_failures():
    m = ExplorerModel(route(["vendor360"], "V-001"), [], fail_explore=ModelError("Azure OpenAI request failed: timeout"))
    findings = explorer(m).run(task())
    assert findings["status"] == "failed" and findings["observations"] == []
    assert findings["notes"][0]["code"] == "exploration_model_unavailable"
    sql = FixedExecutor(QueryFailed("PostgreSQL error 42703: column does not exist"))
    m = model([step("run_sql", MASTER), step("finish")])
    findings = explorer(m, sql=sql).run(task())
    assert m.requests[1][1]["observations"][0]["message"] == "PostgreSQL error 42703: column does not exist"


def test_explorer_payload_shows_budget_catalog_and_certified_context():
    m = model([step("search_catalog", concepts=["Assignment.assignment_id"]), step("finish")])
    explorer(m, cypher=FixedExecutor(), graph_schema=graph_schema(OntologyRegistry.load())).run(task())
    first = m.requests[0][1]
    assert first["budget_left"] == {"queries": 6, "steps": 9, "searches": 3, "retries": 2, "follow_links": 3}
    assert first["certified_findings"] == ["V-001 is Aurelix Codeworks."]
    assert {"clm.canonical_vendor_master", "workforce.ct_workforce_organization"} <= {d["dataset"] for d in first["catalog"]}
    assert "Vendor" in first["business_graph"]["labels"] and "Dataset" not in first["business_graph"]["labels"]
    assert "worker_alias" not in first["business_graph"]["labels"]["Assignment"]
    assert first["available_actions"] == ["search_catalog", "run_sql", "run_cypher", "finish"]
    second = m.requests[1][1]["observations"][0]
    assert second["id"] == "C1" and any(r["dataset"] == "workforce.ct_workforce_organization" for r in second["results"])
    assert len(json.dumps(first)) < 50000


def test_explorer_runs_guarded_cypher_with_host_bound_namespace():
    graph = FixedExecutor({"columns": ["c.contract_id", "k"], "rows": [["CTR-001", {"labels": ["RenewalClause"], "notice_days": 90}]],
                           "truncated": False})
    m = model([step("run_cypher", "MATCH (c:Contract {_kg_namespace: $namespace, contract_id: 'CTR-001'})-[:HAS_CLAUSE]->(k) "
                                  "RETURN c.contract_id, k", graph="business"), step("finish", keep=["Q1"])])
    findings = explorer(m, cypher=graph).run(task())
    assert graph.queries[0].graph == "business" and "Contract" in findings["concepts_found"]
    assert findings["observations"][0]["rows"][0][1]["notice_days"] == 90


# ------------------------------------------------------------------ orchestrator

def supervise(decision, model, sql=None, **kwargs):
    return SupervisorAgent(decision, model, explorer=explorer(model, sql=sql), **kwargs)


def test_exploring_answer_keeps_certified_values_and_cites_source_rows(decision):
    question, routing, calls = DEMOS[0]
    m = ExplorerModel(routing, calls, {"vendor360": [step("run_sql", FORECAST), step("run_sql", WORKFORCE, purpose="follow_link"),
                                                     step("finish", keep=["Q1", "Q2"])]})
    result = supervise(decision, m).ask(question)
    assert result["status"] == "answered"
    assert "4 representative workforce assignments" in result["final_answer"] and "SLA breach yes" in result["final_answer"]
    assert [t["tool"] for t in result["tool_results"]] == ["get_vendor_360", "run_sql", "run_sql"]
    assert "Source rows Q1 (finance.ct_vendor_technology_forecast), row 1 of 1: Contract_Number=CTR-001; Col_2026_YTP=3329863.01" in result["final_answer"]
    card = next(c for c in result["facts"] if c["text"].startswith("Source rows Q1"))
    evidence = {e["evidence_id"]: e["source"] for e in result["evidence"]}
    # The card's evidence is the query itself: what ran, where, and every row it returned.
    source = evidence[card["evidence_ids"][0]]
    assert (source["source_type"], source["query_language"], source["query_id"]) == ("postgres", "sql", "Q1")
    assert source["query"].startswith('SELECT "Contract_Number", "Col_2026_YTP", "_source_line" FROM "finance"')
    assert source["rows"] == [["CTR-001", "3329863.01", "2"]] and source["row_count"] == 1
    assert source["records"] == ["finance.ct_vendor_technology_forecast:line 2"] and source["specialist"] == "vendor360"
    workforce = next(e["source"] for e in result["evidence"] if e["source"].get("query_id") == "Q2")
    assert workforce["purpose"] == "follow_link" and len(workforce["rows"]) == 4
    sources = {s["source"]: s for s in result["sources_used"]}
    assert sources["finance.ct_vendor_technology_forecast"]["via"] == ["run_sql"]
    assert "canonical_vendor_master" in sources and sources["canonical_vendor_master"]["via"] == ["get_vendor_360"]
    assert result["findings"][0]["kept"] == ["Q1", "Q2"] and result["specialists_used"] == ["vendor360"]
    # The explorer is told the data snapshot date; the wall clock is never offered.
    explore_payload = next(p for stage, p, _ in m.requests if stage == "explore_vendor360")
    assert explore_payload["task"]["as_of_date"] == "2026-09-28"
    assert any(n["code"] == "exploration_supplementary" for n in result["limitations"])
    assert not any(n["code"] in ("contradiction", "coverage_gap") for n in result["limitations"])
    nodes = [e["node"] for e in result["trace"]]
    assert nodes == ["guard", "route", "resolve", "plan", "validate", "execute.tool", "execute", "explore", "verify", "ground", "synthesize"]
    explore = next(e for e in result["trace"] if e["node"] == "explore")
    assert explore["agent"] == "vendor360" and [s["status"] for s in explore["steps"] if s["node"] == "explore.act"] == ["ok", "ok", "ok"]
    json.dumps(result)


def test_multi_dataset_rows_cite_the_query_not_an_ambiguous_line(decision):
    question, routing, calls = DEMOS[0]
    join = ('SELECT m."Contract_ID", f."Col_2026_YTP", m._source_line FROM clm.canonical_vendor_master m '
            'JOIN finance.ct_vendor_technology_forecast f ON f."Contract_Number" = m."Contract_ID" WHERE m."Vendor_ID" = \'V-001\'')
    m = ExplorerModel(routing, calls, {"vendor360": [step("run_sql", join), step("finish", keep=["Q1"])]})
    result = supervise(decision, m).ask(question)
    tool = next(t for t in result["tool_results"] if t["tool"] == "run_sql")
    [source] = tool["result"]["evidence"]
    assert source["source_dataset"] == "clm.canonical_vendor_master, finance.ct_vendor_technology_forecast"
    assert source["query"] == tool["arguments"]["query"] and source["records"] == [] and source["rows"]
    card = next(c for c in result["facts"] if c["text"].startswith("Source rows Q1"))
    assert len(card["evidence_ids"]) == len(set(card["evidence_ids"])) == 1


def test_parallel_explorers_one_per_specialist(decision):
    question, routing, calls = DEMOS[11]
    m = ExplorerModel(routing, calls, {name: [step("run_sql", MASTER.replace("V-001", "V-009")), step("finish", keep=["Q1"])]
                                       for name in ("renewal", "risk_dependency")})
    result = supervise(decision, m).ask(question)
    assert result["status"] == "answered" and result["specialists_used"] == routing["specialists"]
    assert [t["tool_id"] for t in result["tool_results"][:3]] == ["T1", "T2", "T3"]
    assert [f["specialist"] for f in result["findings"]] == ["renewal", "risk_dependency", "spend_forecast"]
    # Identical queries from two specialists become one tool result.
    assert [t["tool"] for t in result["tool_results"]].count("run_sql") == 1
    assert {s for stage, *_ in m.requests if stage.startswith("explore_") for s in [stage]} == {
        "explore_renewal", "explore_risk_dependency", "explore_spend_forecast"}
    assert sum(1 for e in result["trace"] if e["node"] == "explore") == 3


def test_concept_gaps_are_redelegated_once(decision):
    question = "Which contracts expire in 0 days?"
    routing, calls = route(["renewal"], focus="renewal"), [call("get_renewal_priorities", days=0)]
    lookup = 'SELECT "Contract_ID", "Contract_End_Date" FROM clm.canonical_vendor_master ORDER BY "Contract_End_Date" LIMIT 3'
    m = ExplorerModel(routing, calls, {"renewal": [step("finish"), step("run_sql", lookup), step("finish", keep=["Q1"])]})
    result = supervise(decision, m).ask(question)
    assert [f["round"] for f in result["findings"]] == [0, 1]
    gap_task = [p for stage, p, _ in m.requests if stage == "explore_renewal"][1]["task"]
    assert gap_task["gaps"] == ["Contract.contract_id", "Contract.end_date"]
    assert not any(n["code"] == "coverage_gap" for n in result["limitations"])
    assert "Source rows Q1 (clm.canonical_vendor_master), row 1 of 3" in result["final_answer"]

    m = ExplorerModel(routing, calls)  # both rounds find nothing
    result = supervise(decision, m).ask(question)
    assert [f["round"] for f in result["findings"]] == [0, 1]
    gap = next(n for n in result["limitations"] if n["code"] == "coverage_gap")
    assert gap["message"] == "renewal: no certified or source rows were found for Contract.contract_id, Contract.end_date; nothing is inferred."


def test_dynamic_value_disagreeing_with_certified_is_flagged(decision):
    question, routing, calls = DEMOS[0]
    doctored = FixedExecutor({"columns": ["Contract_ID", "Contract_End_Date"], "rows": [["CTR-001", "2027-01-01"]], "truncated": False})
    query = 'SELECT "Contract_ID", "Contract_End_Date" FROM finance.ct_vendor_technology_forecast WHERE "Contract_ID" = \'CTR-001\''
    m = ExplorerModel(routing, calls, {"vendor360": [step("run_sql", query), step("finish", keep=["Q1"])]})
    result = supervise(decision, m, sql=doctored).ask(question)
    note = next(n for n in result["limitations"] if n["code"] == "contradiction")
    assert note["message"] == ("Contradiction: finance.ct_vendor_technology_forecast.Contract_End_Date is 2027-01-01 for CTR-001, "
                               "but the certified Contract.end_date is 2026-12-27. The certified value is used.")
    assert "contract expires 2026-12-27" in result["final_answer"] and "certified value is used" in result["final_answer"]


def test_portfolio_question_explores_vendor_scoped_specialists_portfolio_wide(decision):
    routing = route(["renewal", "spend_forecast"], focus="renewal")
    forecast = 'SELECT "Contract_Number", "Col_2026_YTP", _source_line FROM finance.ct_vendor_technology_forecast LIMIT 5'
    m = ExplorerModel(routing, [call("get_renewal_priorities", days=90)],
                      {"spend_forecast": [step("run_sql", forecast), step("finish", keep=["Q1"])]})
    result = supervise(decision, m).ask("Which contracts end in the next 90 days and what forecast records are linked?")
    assert result["status"] == "answered" and result["route"] == routing
    note = next(n for n in result["limitations"] if n["code"] == "portfolio_exploration")
    assert note["message"].startswith("spend_forecast has no certified portfolio tool")
    assert all(f"{v}: contract expires" in result["final_answer"] for v in ("V-005", "V-018", "V-009", "V-007", "V-013", "V-001"))
    # spend_forecast is never asked to plan a certified call; its explorer runs across the portfolio.
    assert not any(stage == "spend_forecast" for stage, *_ in m.requests)
    payload = next(p for stage, p, _ in m.requests if stage == "explore_spend_forecast")
    assert payload["task"]["scope"] == "portfolio" and payload["task"]["vendor_ids"] == []
    assert [f["specialist"] for f in result["findings"]] == ["renewal", "spend_forecast"]
    assert any(t["tool"] == "run_sql" and t["specialist"] == "spend_forecast" for t in result["tool_results"])


def test_open_ended_risk_question_runs_the_risk_explorer_without_a_vendor(decision):
    routing = route(["risk_dependency"], focus="risk")
    risk = ('SELECT "Vendor_ID", "Contract_ID", "Contract_End_Date", _source_line FROM clm.canonical_vendor_master '
            'ORDER BY "Contract_End_Date" LIMIT 5')
    m = ExplorerModel(routing, [], {"risk_dependency": [step("run_sql", risk), step("finish", keep=["Q1"])]})
    result = supervise(decision, m).ask("What contracts are at major risk?")
    assert result["status"] == "answered" and result["specialists_used"] == ["risk_dependency"]
    assert [e["node"] for e in result["trace"]][:4] == ["guard", "route", "resolve", "validate"]
    assert "Source rows Q1 (clm.canonical_vendor_master)" in result["final_answer"]
    assert any(n["code"] == "portfolio_exploration" for n in result["limitations"])
    # Nothing found anywhere: an honest not-found, never an invented answer.
    result = supervise(decision, ExplorerModel(routing, [])).ask("What contracts are at major risk?")
    assert result["status"] == "not_found" and result["final_answer"].startswith("No certified or source rows")
    # Without an explorer the question says what it needs instead of a generic validation error.
    result = SupervisorAgent(decision, ExplorerModel(routing, [])).ask("What contracts are at major risk?")
    assert result["status"] == "clarification" and result["final_answer"].startswith("This question needs a named vendor")


def test_declining_specialist_is_dropped_only_beside_a_valid_plan(decision):
    routing = route(["renewal", "what_if"], focus="renewal")
    question = "Which contracts expire in the next 90 days?"
    # what_if has no scripted call, so it declines; renewal still answers and the answer says what was not run.
    result = SupervisorAgent(decision, ExplorerModel(routing, [call("get_renewal_priorities", days=90)])).ask(question)
    assert result["status"] == "answered" and result["specialists_used"] == ["renewal"]
    assert any(n["code"] == "specialist_declined" and n["message"].startswith("what_if found nothing") for n in result["limitations"])
    # An invalid call from any specialist still fails the whole plan closed.
    bad = call("run_workforce_scenario", action="reduce", percentage=50, country="India")
    result = SupervisorAgent(decision, ExplorerModel(routing, [call("get_renewal_priorities", days=90), bad])).ask(question)
    assert result["status"] == "clarification" and not result["tool_results"]
    # When every specialist declines, the question needs clarification.
    result = SupervisorAgent(decision, ExplorerModel(routing, [])).ask(question)
    assert result["status"] == "clarification"


def test_exploration_failure_still_returns_the_certified_answer(decision):
    question, routing, calls = DEMOS[6]
    m = ExplorerModel(routing, calls, fail_explore=ModelError("Azure OpenAI request failed: rate_or_quota"))
    result = supervise(decision, m).ask(question)
    assert result["status"] == "answered" and "294,920.56" in result["final_answer"]
    assert any(n["code"] == "exploration_model_unavailable" for n in result["limitations"])
    assert "rate_or_quota" not in json.dumps(result)


def test_answer_stage_failure_is_unavailable_not_an_empty_answer(decision, monkeypatch):
    import citi_project.services.agents.graph as graph

    def broken(tool_results):
        raise RuntimeError("grounding bug")
    monkeypatch.setattr(graph, "build_grounding", broken)
    question, routing, calls = DEMOS[6]
    result = SupervisorAgent(decision, ExplorerModel(routing, calls)).ask(question)
    assert result["status"] == "unavailable" and result["final_answer"].startswith("A deterministic tool could not complete")
    assert [e["node"] for e in result["trace"]][-2:] == ["ground", "stop"]


def test_graph_runs_from_a_question_alone_as_studio_does(decision):
    question, routing, calls = DEMOS[0]
    m = ExplorerModel(routing, calls, {"vendor360": [step("run_sql", FORECAST), step("finish", keep=["Q1"])]})
    graph = supervise(decision, m).orchestrator.graph
    out = graph.invoke({"question": question})
    assert set(out) == {"final_answer", "result", "trace"}
    assert "4 representative workforce assignments" in out["final_answer"] and out["result"]["status"] == "answered"
    m.route_result, m.calls = route(["vendor360"], focus="workforce", active=True), [call("get_vendor_360", vendor_id="V-005")]
    follow = graph.invoke({"question": "What about its workforce?", "active_vendor_id": "V-005"})
    assert follow["result"]["resolved_entities"]["vendor_ids"] == ["V-005"]
    # Each specialist is its own node, and each explorer is a sub-graph Studio can expand.
    names = ("vendor360", "renewal", "risk_dependency", "rationalization", "spend_forecast", "what_if")
    nodes = set(graph.get_graph().nodes)
    assert {"guard", "route", "resolve", "validate", "execute", "verify", "ground", "synthesize", "stop"} <= nodes
    assert {f"plan_{n}" for n in names} | {f"explore_{n}" for n in names} <= nodes
    inner = set(graph.get_graph(xray=True).nodes)
    assert {"explore_renewal:discover", "explore_renewal:decide", "explore_renewal:act", "explore_renewal:report"} <= inner


def test_early_stops_skip_exploration(decision):
    m = ExplorerModel(route(["vendor360"], "V-999"), [])
    result = supervise(decision, m).ask("What about V-999?", state=ConversationState("V-001"))
    assert result["status"] == "not_found" and result["findings"] == []
    assert [e["node"] for e in result["trace"]] == ["guard", "route", "stop"]
    assert not any(stage.startswith("explore_") for stage, *_ in m.requests)


# ------------------------------------------------------------------ executors

class FakeCursor:
    def __init__(self, conn):
        self.conn, self.description = conn, None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))
        if self.conn.error and params is None:
            raise self.conn.error
        self.description = [SimpleNamespace(name="Vendor_ID")]

    def fetchmany(self, n):
        self.conn.fetched = n
        return [("V-001",), ("V-002",), ("V-003",)][:n]


class FakeConnection:
    def __init__(self, error=None):
        self.executed, self.error, self.read_only, self.closed = [], error, False, False

    def cursor(self):
        return FakeCursor(self)

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def test_sql_executor_is_read_only_bounded_and_sanitized():
    import psycopg
    conn = FakeConnection()
    guarded = guard_sql('SELECT "Vendor_ID" FROM clm.canonical_vendor_master', schema_index(), max_rows=2)
    executor = SqlExecutor("postgresql://citi_app:placeholder@localhost/db", max_rows=2, timeout_ms=5000, connect=lambda dsn: conn)
    result = executor.run(guarded)
    assert conn.read_only and conn.closed and conn.fetched == 3
    assert conn.executed == [("SELECT set_config('statement_timeout', %s, true)", ("5000",)), (guarded.sql, None)]
    assert result == {"columns": ["Vendor_ID"], "rows": [["V-001"], ["V-002"]], "truncated": True}
    assert "placeholder" not in repr(executor)
    error = psycopg.errors.UndefinedColumn("column \"x\" does not exist")
    with pytest.raises(QueryFailed, match="PostgreSQL error"):
        SqlExecutor("postgresql://x", connect=lambda dsn: FakeConnection(error)).run(guarded)

    def refuse(dsn):
        raise psycopg.OperationalError("password authentication failed for postgresql://citi_app:placeholder@host")
    with pytest.raises(QueryFailed) as exc:
        SqlExecutor("postgresql://citi_app:placeholder@host/db", connect=refuse).run(guarded)
    assert str(exc.value) == "PostgreSQL is unavailable"


class FakeNode(dict):
    def __init__(self, labels, props):
        super().__init__(props)
        self.labels = labels


class FakeGraphClient:
    def __init__(self, records=None, error=None):
        self.records, self.error, self.calls = records or [], error, []

    def read(self, callback):
        client = self

        class Tx:
            def run(self, query, **params):
                client.calls.append((query, params))
                if client.error:
                    raise client.error
                return iter(client.records)
        return callback(Tx())


def test_cypher_executor_binds_namespace_caps_rows_and_strips_private_fields():
    node = FakeNode({"CitiKGEntity", "Assignment"}, {"assignment_id": "ASN-001-001", "worker_alias": "W-1", "_kg_key": "k"})
    client = FakeGraphClient([{"a": node, "n": 1}, {"a": node, "n": 2}, {"a": node, "n": 3}])
    executor = CypherExecutor(client, {"business": "synthetic-pack-20260928"}, max_rows=2, pii_properties=pii_properties(OntologyRegistry.load()))
    guarded = guard_cypher("MATCH (a:Assignment {_kg_namespace: $namespace}) RETURN a, 1 AS n", graph="business")
    result = executor.run(guarded)
    assert client.calls[0][1] == {"namespace": "synthetic-pack-20260928"}
    assert result["rows"][0][0] == {"labels": ["Assignment"], "assignment_id": "ASN-001-001"} and result["truncated"]
    failing = FakeGraphClient(error=SimpleNamespace())
    error = type("ClientError", (Exception,), {"code": "Neo.ClientError.Statement.SyntaxError", "message": "Invalid input 'X'"})()
    failing.error = error
    with pytest.raises(QueryFailed, match="Neo.ClientError.Statement.SyntaxError: Invalid input 'X'"):
        CypherExecutor(failing, {"business": "ns"}).run(guarded)
    with pytest.raises(QueryFailed, match="not available"):
        CypherExecutor(client, {"business": "ns"}).run(guard_cypher("MATCH (d:Dataset {_kg_namespace: $namespace}) RETURN d", graph="catalog"))
