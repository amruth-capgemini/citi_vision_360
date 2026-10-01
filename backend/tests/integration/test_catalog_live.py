"""Opt-in live catalog tests: Postgres harvest parity, and ingestion into an isolated Neo4j namespace."""

from dataclasses import replace
import os
import uuid

import pytest

from citi_project.services.catalog import (CatalogQueryService, Classifier, PostgresCatalogSource, build_payload, harvest,
                                           load_catalog_registry, summarize)
from citi_project.services.knowledge_graph.client import Neo4jClient
from citi_project.services.knowledge_graph.config import Neo4jConfig
from citi_project.services.knowledge_graph.service import KnowledgeGraphService
from citi_project.services.ontology import OntologyRegistry
from citi_project.services.postgres import PostgresConfig
from citi_project.services.postgres.tables import SYSTEMS
from test_catalog import MemorySource


@pytest.fixture(scope="module")
def pg_harvest(request):
    if not request.config.getoption("--pg-integration"):
        pytest.skip("Live PostgreSQL tests require --pg-integration")
    config = PostgresConfig.from_env()
    if not config.reader.strip():
        pytest.skip("CITI_PG_READER_DSN must be set in this process")
    import psycopg

    with psycopg.connect(config.reader_dsn) as conn:
        conn.read_only = True
        return harvest(PostgresCatalogSource(conn), list(SYSTEMS))


@pytest.mark.pg_integration
def test_postgres_harvest_matches_loaded_csvs(pg_harvest):
    expected = harvest(MemorySource(), list(SYSTEMS))
    assert pg_harvest.anchors == expected.anchors
    assert set(pg_harvest.schemas) == set(SYSTEMS)
    for actual, wanted in zip(pg_harvest.tables, expected.tables, strict=True):
        assert (actual.dataset_id, actual.row_count, actual.primary_key, actual.sample_rows) == \
            (wanted.dataset_id, wanted.row_count, wanted.primary_key, wanted.sample_rows)
        assert actual.comment  # loaded from schema_lineage.json by citi-pg
        for a, w in zip(actual.columns, wanted.columns, strict=True):
            assert replace(a, comment=None) == w, (actual.dataset_id, a.name)


@pytest.mark.pg_integration
@pytest.mark.neo4j_integration
def test_catalog_ingests_validates_and_answers_queries(request, pg_harvest):
    if not request.config.getoption("--neo4j-integration"):
        pytest.skip("Live Neo4j tests require --neo4j-integration")
    database = os.environ.get("NEO4J_TEST_DATABASE", "")
    if not database.startswith("kg-test-"):
        pytest.fail("NEO4J_TEST_DATABASE must name a dedicated database beginning kg-test-")
    env = {name: os.environ.get(name, "") for name in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_TRANSPORT", "NEO4J_QUERY_API_URL")}
    env["NEO4J_DATABASE"] = database
    namespace = "integration-catalog-" + uuid.uuid4().hex
    catalog = load_catalog_registry()
    classification = Classifier(OntologyRegistry.load()).classify(pg_harvest)
    payload = build_payload(pg_harvest, classification, catalog, OntologyRegistry.load(), namespace=namespace)
    with Neo4jClient(Neo4jConfig.from_env(env)) as client:
        service = KnowledgeGraphService(catalog, client)
        service.ensure_schema()
        try:
            first, second = service.ingest(payload), service.ingest(payload)
            assert second.written_entities == second.written_relationships == 0
            assert first.total_entities == sum(summarize(payload)["entities"].values())
            assert service.validate(namespace, payload, exact=True).valid
            query = CatalogQueryService(client, namespace)
            sources = query.find_sources(["Contract.end_date", "RiskAssessment.risk_tier"])
            assert {s["dataset_id"] for s in sources["sources"]["Contract.end_date"]} >= {"clm.canonical_vendor_master", "finance.ct_technology_financials"}
            assert len(query.datasets_for_contract("CTR-001")) == 7
            described = query.describe_dataset("finance.ct_technology_financials")
            assert described["linked_contracts"] == 20 and len(described["fields"]) == 84
            plan = query.join_plan("Contract.end_date")
            assert plan["homes"][0]["home_dataset"] == "clm.canonical_vendor_master"
            joined = {(j["dataset"], j["field"]) for j in plan["homes"][0]["joins"]}
            assert ("finance.ct_vendor_technology_forecast", "Contract_Number") in joined
            assert ("workforce.ct_workforce_organization", "Contract_ID") in joined
        finally:
            client.write(lambda tx: tx.run("MATCH (n:CitiKGEntity {_kg_namespace: $namespace}) DETACH DELETE n", namespace=namespace).consume())
            client.write(lambda tx: tx.run("MATCH (s:CitiKGScope {namespace: $namespace}) DELETE s", namespace=namespace).consume())
