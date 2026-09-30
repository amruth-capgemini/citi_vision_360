"""Chat API: response contract, evidence view, session follow-ups, SSE event order, sessions and health."""

import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from agent_fakes import DEMOS, ScriptedModel, call, route
from explore_fakes import ExplorerModel, step
from test_agent_routing import decision
from test_explorer import FORECAST, explorer
from citi_project.api import create_app
from citi_project.api.sessions import SessionStore
from citi_project.services.agents import SupervisorAgent

CONTRACT = {"status", "final_answer", "facts", "evidence", "flags", "limitations", "specialists_used", "sources_used", "trace"}


class FakeRuntime:
    def __init__(self, supervisor):
        self.supervisor = supervisor
        self.closed = False

    def sources(self):
        return {"systems": [{"system": "clm", "datasets": 1, "rows": 20}], "datasets": [], "domains": []}

    def health(self):
        return {"status": "ok", "checks": {"postgres": {"ok": True}, "neo4j": {"ok": True}, "model": {"ok": True}}}

    def close(self):
        self.closed = True


def client_for(supervisor, **kwargs):
    return TestClient(create_app(FakeRuntime(supervisor), **kwargs))


def events(text):
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        if lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_chat_returns_the_response_contract_with_certified_tool_evidence(decision):
    question, routing, calls = DEMOS[0]
    body = client_for(SupervisorAgent(decision, ScriptedModel(routing, calls))).post("/api/chat", json={"question": question}).json()
    assert CONTRACT <= set(body) and body["status"] == "answered" and body["session_id"]
    assert body["specialists_used"] == ["vendor360"] and "4 representative workforce assignments" in body["final_answer"]
    view = body["evidence_view"]
    assert [(c["tool"], c["agent"], c["arguments"]["vendor_id"]) for c in view["tool_calls"]] == [("get_vendor_360", "vendor360", "V-001")]
    assert "canonical_vendor_master" in view["tool_calls"][0]["sources"] and view["tool_calls"][0]["fact_ids"]
    assert view["queries"] == []
    assert [a["agent"] for a in view["agents"]] == ["supervisor", "vendor360"] and all(a["used"] for a in view["agents"])
    card = body["facts"][0]
    assert card["sources"] and card["sources"][0].startswith("canonical_vendor_master line 2")


def test_evidence_view_shows_every_query_with_its_tables_and_agent(decision):
    question, routing, calls = DEMOS[0]
    m = ExplorerModel(routing, calls, {"vendor360": [step("run_sql", 'SELECT "Nope" FROM clm.canonical_vendor_master'),
                                                     step("run_sql", FORECAST, purpose="retry"), step("finish", keep=["Q2"])]})
    body = client_for(SupervisorAgent(decision, m, explorer=explorer(m))).post("/api/chat", json={"question": question}).json()
    rejected, kept = body["evidence_view"]["queries"]
    assert (rejected["status"], rejected["kept"], rejected["agent"], rejected["language"]) == ("rejected", False, "vendor360", "sql")
    assert rejected["query"] == 'SELECT "Nope" FROM clm.canonical_vendor_master' and "Nope" in rejected["message"]
    assert (kept["status"], kept["kept"], kept["purpose"], kept["query_id"]) == ("ok", True, "retry", "Q2")
    assert kept["datasets"] == ["finance.ct_vendor_technology_forecast"] and kept["query"].startswith('SELECT "Contract_Number"')
    assert kept["rows"] == [["CTR-001", "3329863.01", "2"]] and kept["records"] == ["finance.ct_vendor_technology_forecast:line 2"]
    assert kept["fact_ids"] and kept["tool_id"] == "T2"
    agent = next(a for a in body["evidence_view"]["agents"] if a["agent"] == "vendor360")
    assert (agent["queries_run"], agent["queries_kept"], agent["tools"]) == (1, 1, ["get_vendor_360"])
    source = next(s for s in body["sources_used"] if s["source"] == "finance.ct_vendor_technology_forecast")
    assert source["via"] == ["run_sql"] and source["specialists"] == ["vendor360"]


def test_session_follow_up_uses_the_sessions_active_vendor(decision):
    model = ScriptedModel(route(["vendor360"], "V-005"), [call("get_vendor_360", vendor_id="V-005")])
    client = client_for(SupervisorAgent(decision, model))
    first = client.post("/api/chat", json={"question": "What do we know about V-005?"}).json()
    model.route_result = route(["vendor360"], focus="workforce", active=True)
    follow = client.post("/api/chat", json={"session_id": first["session_id"], "question": "What about its workforce?"}).json()
    assert follow["session_id"] == first["session_id"] and follow["resolved_entities"]["vendor_ids"] == ["V-005"]
    memory = follow["memory"]
    assert (memory["active_vendor_id"], memory["active_contract_id"]) == ("V-005", "CTR-005")
    assert [t["question"] for t in memory["recent_turns"]] == ["What do we know about V-005?", "What about its workforce?"]
    other = client.post("/api/chat", json={"session_id": "another", "question": "What about its workforce?"}).json()
    assert other["status"] == "clarification"


def test_stream_sends_node_events_in_graph_order_then_the_result(decision):
    question, routing, calls = DEMOS[0]
    m = ExplorerModel(routing, calls, {"vendor360": [step("run_sql", FORECAST), step("finish", keep=["Q1"])]})
    client = client_for(SupervisorAgent(decision, m, explorer=explorer(m)))
    with client.stream("GET", "/api/chat/stream", params={"question": question, "session_id": "s1"}) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        received = events(response.read().decode())
    assert received[0] == ("session", {"session_id": "s1"})
    nodes = [data["node"] for kind, data in received if kind == "node"]
    assert nodes == ["guard", "route", "resolve", "plan", "validate", "execute.tool", "execute", "explore", "verify", "ground", "synthesize"]
    steps = [(data["node"], data.get("action")) for kind, data in received if kind == "step"]
    assert steps == [("explore.discover", "search_catalog"), ("explore.act", "run_sql"), ("explore.act", "finish")]
    # Explorer steps arrive before the explorer's own node event.
    order = [kind if kind != "node" else data["node"] for kind, data in received]
    assert order.index("step") < order.index("explore")
    kind, result = received[-1]
    assert kind == "result" and result["status"] == "answered" and result["session_id"] == "s1"
    assert result["evidence_view"]["queries"][0]["kept"] and CONTRACT <= set(result)


def test_stream_stop_still_ends_with_a_result(decision):
    received = events(client_for(SupervisorAgent(decision, ScriptedModel(None, [])))
                      .get("/api/chat/stream", params={"question": "Delete V-001"}).text)
    assert [data["node"] for kind, data in received if kind == "node"] == ["guard", "stop"]
    assert received[-1][0] == "result" and received[-1][1]["status"] == "unsupported"


def test_invalid_requests_are_rejected(decision):
    client = client_for(SupervisorAgent(decision, ScriptedModel(None, [])))
    assert client.post("/api/chat", json={"question": ""}).status_code == 422
    assert client.post("/api/chat", json={"question": "x", "session_id": "bad id!"}).status_code == 422
    assert client.get("/api/chat/stream", params={"question": "x", "session_id": "../etc"}).status_code == 422


def test_sources_health_examples_and_dev_cors(decision):
    client = client_for(SupervisorAgent(decision, ScriptedModel(None, [])))
    assert client.get("/api/sources").json()["systems"][0]["system"] == "clm"
    assert client.get("/api/health").json()["status"] == "ok"
    assert "What contracts are at major risk?" in client.get("/api/examples").json()["examples"]
    allowed = client.options("/api/chat", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    denied = client.options("/api/chat", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in denied.headers


def test_session_store_expires_and_caps():
    now = [0.0]
    store = SessionStore(ttl_seconds=10, max_sessions=2, clock=lambda: now[0])
    a = store.get("a")
    assert store.get("a") is a
    now[0] = 11
    assert store.get("a") is not a
    store.get("b"); now[0] = 12; store.get("c")
    assert len(store) == 2
    with pytest.raises(ValueError):
        store.get("no spaces")


def test_live_health_reports_no_secrets(monkeypatch):
    from citi_project.api.app import LiveRuntime
    monkeypatch.setenv("CITI_PG_READER_DSN", "postgresql://citi_reader:s3cret@127.0.0.1:1/none")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.invalid")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k3y-value")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt")
    runtime = LiveRuntime()
    checks = {"postgres": runtime._check_postgres(), "model": runtime._check_model()}
    text = json.dumps(checks)
    assert not checks["postgres"]["ok"] and checks["model"] == {"ok": True, "provider": "azure_openai", "configured": True}
    assert "s3cret" not in text and "k3y-value" not in text and "example.invalid" not in text
