"""Query API wire contract tests; no sockets or live credentials."""
import base64
from copy import deepcopy
import json
import ssl
from unittest.mock import patch

import pytest

from citi_project.services.knowledge_graph.client import GraphConnectionError, Neo4jClient
from citi_project.services.knowledge_graph.config import Neo4jConfig, GraphConfigurationError
from citi_project.services.knowledge_graph.http_client import QueryAPIClient, QueryResult, _NoRedirect
from citi_project.services.knowledge_graph.transport import create_client


def config(**kwargs):
    return Neo4jConfig("", "test-user", "test-password", "neo4j", transport="http",
                       query_api_url=kwargs.pop("query_api_url", "https://example.invalid"), **kwargs)


class Response:
    status = 202

    def __init__(self, body, headers=None):
        self.body, self.headers = body, headers or {}

    def read(self):
        return json.dumps(self.body).encode() if self.body is not None else b""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


class ScriptedOpener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        assert timeout == 60
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def result(**row):
    return Response({"data": {"fields": list(row), "values": [list(row.values())]}})


def begin():
    return Response({"transaction": {"id": "tx-1"}}, {"neo4j-cluster-affinity": "server-1"})


def commit():
    return Response({"bookmarks": ["bookmark-1"]})


def test_selection_lazy_and_default_bolt():
    assert isinstance(create_client(config()), QueryAPIClient)
    assert isinstance(create_client(Neo4jConfig("bolt://localhost", "u", "p", "db")), Neo4jClient)
    assert repr(config()) == "Neo4jConfig()"
    with patch("citi_project.services.knowledge_graph.http_client.build_opener") as factory:
        with create_client(config()):
            factory.assert_not_called()


@pytest.mark.parametrize("url", ["http://example.invalid", "https://u:p@example.invalid", "https://example.invalid/path",
                                "https://example.invalid?x=1", "https://example.invalid/#x", "https://example.invalid:bad"])
def test_invalid_url(url):
    with pytest.raises(GraphConfigurationError):
        config(query_api_url=url)


def test_env_and_safe_aura_derivation():
    env = {"NEO4J_TRANSPORT": "http", "NEO4J_USERNAME": "test-user", "NEO4J_PASSWORD": "test-password",
           "NEO4J_DATABASE": "db", "NEO4J_URI": "neo4j+s://test.databases.neo4j.io:7687"}
    assert Neo4jConfig.from_env(env).http_endpoint == "https://test.databases.neo4j.io/db/db/query/v2"
    env["NEO4J_URI"] = "bolt://localhost"
    with pytest.raises(GraphConfigurationError, match="NEO4J_QUERY_API_URL"):
        Neo4jConfig.from_env(env)
    env["NEO4J_TRANSPORT"] = "typo"
    with pytest.raises(GraphConfigurationError, match="NEO4J_TRANSPORT"):
        Neo4jConfig.from_env(env)


def test_connectivity_only_read_statement_and_basic_auth():
    opener = ScriptedOpener(result(ok=1))
    with QueryAPIClient(config(), opener=opener) as client:
        assert client.check_connectivity()
    request, = opener.requests
    assert request.full_url == "https://example.invalid/db/neo4j/query/v2"
    assert json.loads(request.data) == {"statement": "RETURN 1 AS ok", "parameters": {}, "accessMode": "Read"}
    assert request.get_header("Authorization") == "Basic " + base64.b64encode(b"test-user:test-password").decode()
    with pytest.raises(GraphConnectionError, match="closed"):
        client.check_connectivity()


def test_explicit_transaction_parameters_affinity_and_bookmarks():
    opener = ScriptedOpener(begin(), result(value="first"), result(value="second"), commit(), begin(), commit())
    client = QueryAPIClient(config(), opener=opener)
    value = "quote ' and unicode \u00e9\n"
    def write(tx):
        assert tx.run("RETURN $value AS value", value=value).single() == {"value": "first"}
        return list(tx.run("RETURN $value AS value", value="second"))
    assert client.write(write) == [{"value": "second"}]
    assert json.loads(opener.requests[1].data)["parameters"] == {"value": value}
    assert all(r.get_header("Neo4j-cluster-affinity") == "server-1" for r in opener.requests[1:4])
    assert opener.requests[3].full_url.endswith("/tx/tx-1/commit")
    client.read(lambda tx: None)
    assert json.loads(opener.requests[4].data) == {"accessMode": "Read", "bookmarks": ["bookmark-1"]}


def test_callback_failure_rolls_back_without_commit():
    opener = ScriptedOpener(begin(), Response(None))
    def fail(tx):
        raise ValueError("caller failure")
    with pytest.raises(ValueError, match="caller failure"):
        QueryAPIClient(config(), opener=opener).write(fail)
    assert [r.method for r in opener.requests] == ["POST", "DELETE"]


def test_202_errors_sanitized_and_rolled_back():
    opener = ScriptedOpener(begin(), Response({"errors": [{"message": "test-password", "code": "secret"}]}), Response(None))
    with pytest.raises(GraphConnectionError) as exc:
        QueryAPIClient(config(), opener=opener).write(lambda tx: tx.run("RETURN 1"))
    assert "test-password" not in str(exc.value)
    assert opener.requests[-1].method == "DELETE"


def test_ambiguous_commit_not_retried():
    opener = ScriptedOpener(begin(), OSError("test-password"))
    with pytest.raises(GraphConnectionError, match="outcome may be unknown"):
        QueryAPIClient(config(), opener=opener).write(lambda tx: 1)
    assert len(opener.requests) == 2


@pytest.mark.parametrize("body", [{}, {"data": []}, {"data": {"fields": ["x"], "values": [[]]}}])
def test_malformed_result(body):
    with pytest.raises(GraphConnectionError):
        QueryResult(body)


def test_tls_verification_and_no_redirects():
    opener = ScriptedOpener(result(ok=1))
    with patch("citi_project.services.knowledge_graph.http_client.build_opener", return_value=opener) as factory:
        assert QueryAPIClient(config()).check_connectivity()
    https, redirect = factory.call_args.args
    assert https._context.verify_mode == ssl.CERT_REQUIRED
    assert https._context.check_hostname
    assert isinstance(redirect, _NoRedirect)
    assert redirect.redirect_request(None, None, 302, "", {}, "https://other.invalid") is None


@pytest.mark.parametrize("failure", [ssl.SSLCertVerificationError("test-password"), OSError("test-password")])
def test_network_errors_are_sanitized(failure):
    with pytest.raises(GraphConnectionError) as exc:
        QueryAPIClient(config(), opener=ScriptedOpener(failure)).check_connectivity()
    assert "test-password" not in str(exc.value)
    assert exc.value.__suppress_context__


def test_http_error_body_not_exposed():
    from io import BytesIO
    from urllib.error import HTTPError
    failure = HTTPError("https://example.invalid", 401, "test-password", {}, BytesIO(b"test-password"))
    with pytest.raises(GraphConnectionError, match="status 401") as exc:
        QueryAPIClient(config(), opener=ScriptedOpener(failure)).check_connectivity()
    assert "test-password" not in str(exc.value)


@pytest.mark.parametrize("command", ["check", "init-schema", "ingest", "validate"])
def test_cli_uses_selected_http_transport(command, tmp_path, capsys):
    from citi_project.services.knowledge_graph.cli import main, sample_payload
    from citi_project.services.knowledge_graph.models import GraphPayload
    from citi_project.services.knowledge_graph.service import KnowledgeGraphService
    from citi_project.services.ontology import OntologyRegistry
    from test_graph_service import MemoryClient
    registry = OntologyRegistry.load()
    memory = MemoryClient(registry)
    payload = sample_payload(registry)
    path = tmp_path / "sample.json"
    path.write_text(json.dumps(payload))
    if command == "validate":
        KnowledgeGraphService(registry, memory).ingest(GraphPayload.from_dict(payload))
    opener = ScriptedOpener(result(ok=1)) if command == "check" else MemoryAPIOpener(memory)
    args = [command]
    if command == "ingest":
        args.append(str(path))
    if command == "validate":
        args.extend(["--namespace", payload["namespace"], "--expected", str(path), "--exact"])
    with patch("citi_project.services.knowledge_graph.cli.Neo4jConfig.from_env", return_value=config()), \
         patch("citi_project.services.knowledge_graph.http_client.build_opener", return_value=opener), \
         patch("citi_project.services.knowledge_graph.client.GraphDatabase.driver") as bolt:
        assert main(args) == 0
        bolt.assert_not_called()
    assert "test-password" not in capsys.readouterr().out


class MemoryAPIOpener:
    """Run existing transaction double through the actual HTTP JSON adapter."""
    def __init__(self, memory):
        self.memory = memory
        self.pending = None

    def open(self, request, timeout):
        from test_graph_service import MemoryTransaction
        body = json.loads(request.data) if request.data else {}
        if request.full_url.endswith("/tx"):
            self.pending = deepcopy(self.memory.state)
            return begin()
        assert request.get_header("Neo4j-cluster-affinity") == "server-1"
        if request.method == "DELETE":
            self.pending = None
            return Response(None)
        if request.full_url.endswith("/commit"):
            self.memory.state = self.pending
            self.pending = None
            return commit()
        from citi_project.services.knowledge_graph.models import GraphInputError
        try:
            rows = MemoryTransaction(self.memory, self.pending).run(body["statement"], **body["parameters"])
        except GraphInputError:
            return Response({"errors": [{"code": "Neo.ClientError.Statement.ExecutionFailed", "message": "simulated"}]})
        fields = list(rows[0]) if rows else []
        return Response({"data": {"fields": fields, "values": [[row[f] for f in fields] for row in rows]}})


def test_service_schema_ingestion_replay_validation_and_rollback():
    from test_graph_service import MemoryClient
    from citi_project.services.ontology import OntologyRegistry
    from citi_project.services.knowledge_graph.cli import sample_payload
    from citi_project.services.knowledge_graph.models import GraphPayload, GraphInputError
    from citi_project.services.knowledge_graph.service import KnowledgeGraphService
    registry = OntologyRegistry.load()
    memory = MemoryClient(registry)
    client = QueryAPIClient(config(), opener=MemoryAPIOpener(memory))
    service = KnowledgeGraphService(registry, client)
    assert service.ensure_schema() > 0
    payload = GraphPayload.from_dict(sample_payload(registry))
    memory.fail_edges = True
    with pytest.raises(GraphConnectionError):
        service.ingest(payload)
    assert memory.state == {"nodes": {}, "edges": {}, "scopes": {}}
    memory.fail_edges = False
    assert service.ingest(payload).written_entities == 4
    assert service.ingest(payload).written_entities == 0
    assert service.validate(payload.namespace, payload, exact=True).valid
    assert len(memory.state["nodes"]) == len(memory.state["edges"]) == 4
