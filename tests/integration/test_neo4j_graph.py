"""Live tests are skipped unless an explicit CLI switch and test database are supplied."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
import uuid

import pytest

from citi_project.services.ontology import OntologyRegistry
from citi_project.services.knowledge_graph import GraphPayload
from citi_project.services.knowledge_graph.cli import sample_payload
from citi_project.services.knowledge_graph.client import Neo4jClient
from citi_project.services.knowledge_graph.config import Neo4jConfig
from citi_project.services.knowledge_graph.service import KnowledgeGraphService

pytestmark = pytest.mark.neo4j_integration


@pytest.fixture
def live_graph(request):
    if not request.config.getoption("--neo4j-integration"):
        pytest.skip("Live Neo4j tests require --neo4j-integration")
    database = os.environ.get("NEO4J_TEST_DATABASE", "")
    if not database.startswith("kg-test-"):
        pytest.fail("NEO4J_TEST_DATABASE must name a dedicated database beginning kg-test-")
    # Normal application NEO4J_DATABASE is never used as a fallback.
    env = {name: os.environ.get(name, "") for name in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD")}
    env["NEO4J_DATABASE"] = database
    namespace = "integration-" + uuid.uuid4().hex
    registry = OntologyRegistry.load()
    with Neo4jClient(Neo4jConfig.from_env(env)) as client:
        assert client.check_connectivity()
        service = KnowledgeGraphService(registry, client)
        service.ensure_schema()
        service.ensure_schema()
        payload = replace(GraphPayload.from_dict(sample_payload(registry)), namespace=namespace)
        try:
            yield service, payload
        finally:
            # Only this test's freshly generated namespace is eligible for cleanup.
            client.write(lambda tx: tx.run(
                "MATCH (n:CitiKGEntity {_kg_namespace: $namespace}) DETACH DELETE n", namespace=namespace).consume())
            client.write(lambda tx: tx.run(
                "MATCH (s:CitiKGScope {namespace: $namespace}) DELETE s", namespace=namespace).consume())


def test_live_ingestion_replay_update_and_validation(live_graph):
    service, payload = live_graph
    first = service.ingest(payload)
    second = service.ingest(payload)
    assert first.total_entities == second.total_entities == 4
    assert first.total_relationships == second.total_relationships == 4
    assert second.written_entities == second.written_relationships == 0
    assert service.validate(payload.namespace, payload, exact=True).valid
    clause = payload.entities[-1]
    updated = replace(clause, properties={**clause.properties, "section_title": "Changed"}, revision=2)
    service.upsert_entity(payload.namespace, updated)
    expected = replace(payload, entities=(*payload.entities[:-1], updated))
    assert service.validate(payload.namespace, expected, exact=True).valid


def test_live_concurrent_replays_do_not_duplicate(live_graph):
    service, payload = live_graph
    # Initialize the single shared driver before concurrent per-call sessions.
    service.client.check_connectivity()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(service.ingest, (payload, payload)))
    assert all(r.total_entities == 4 and r.total_relationships == 4 for r in results)
    assert service.validate(payload.namespace, payload, exact=True).valid
