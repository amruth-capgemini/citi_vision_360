"""Opt-in live PostgreSQL tests: load, verify, idempotency, CSV parity and reader privileges."""

from pathlib import Path

import pytest

from citi_project.services.decision_intelligence import DecisionIntelligenceService
from citi_project.services.postgres import PostgresConfig
from citi_project.services.postgres import loader
from citi_project.services.semantic_service import VendorSemanticService
from citi_project.services.structured_data import StructuredQueryService
from test_semantic_service import canonical_graph

DATA = Path(__file__).resolve().parents[3] / "initial_plan"
pytestmark = pytest.mark.pg_integration

# Every protected result in the implementation plan index §3, plus the raw query surface.
PROTECTED = [
    ("get_renewal_priorities", {}),
    ("get_spend_forecast_analysis", {"vendor_id": "V-005"}),
    ("get_spend_forecast_analysis", {"vendor_id": "V-009"}),
    ("get_vendor_360", {"vendor_id": "V-001"}),
    ("get_vendor_dependency_risk", {"vendor_id": "V-009"}),
    ("get_renewal_context", {"vendor_id": "V-017"}),
    ("get_vendor_rationalization_opportunities", {}),
    ("get_vendor_rationalization_opportunities", {"organization_id": "ORG-01"}),
    ("run_workforce_scenario", {"country": "India"}),
    ("run_workforce_scenario", {"action": "reduce", "percentage": 20, "country": "India", "worker_type": "Contractor"}),
    ("run_workforce_scenario", {"action": "shift", "percentage": 100, "country": "India", "worker_type": "Contractor",
                                "target_worker_type": "Consultant", "assignment_ids": ["ASN-004-002", "ASN-001-002"]}),
]


@pytest.fixture(scope="module")
def config(request):
    if not request.config.getoption("--pg-integration"):
        pytest.skip("Live PostgreSQL tests require --pg-integration")
    config = PostgresConfig.from_env()
    if not config.owner.strip() or not config.reader.strip():
        pytest.skip("CITI_PG_DSN and CITI_PG_READER_DSN must both be set in this process")
    return config


@pytest.fixture(scope="module")
def owner(config):
    import psycopg

    with psycopg.connect(config.owner_dsn, autocommit=True) as conn:
        loader.init(conn)
        loader.load(conn, DATA)
        yield conn


def test_load_verify_and_reload_changes_nothing(owner):
    assert loader.verify(owner, DATA)["valid"] is True
    report = loader.load(owner, DATA)
    assert {r["action"] for r in report.values()} == {"unchanged"}
    assert loader.verify(owner, DATA)["valid"] is True


def test_parity_with_csv(owner, config):
    from_csv = StructuredQueryService(DATA)
    from_pg = StructuredQueryService.from_postgres(config)
    assert from_pg._tables == from_csv._tables
    services = [DecisionIntelligenceService(VendorSemanticService(s, canonical_graph(s))) for s in (from_csv, from_pg)]
    for method, kwargs in PROTECTED:
        assert getattr(services[1], method)(**kwargs) == getattr(services[0], method)(**kwargs), method
    for vendor in sorted(from_csv._masters):
        for method in ("get_vendor_contract", "get_budget_forecast_actual", "get_vendor_workforce", "get_vendor_forecast",
                       "get_vendor_application_bridge", "get_vendor_financials"):
            assert getattr(from_pg, method)(vendor) == getattr(from_csv, method)(vendor), (method, vendor)


@pytest.mark.parametrize("statement", [
    'INSERT INTO "mdm"."organization_ou_crosswalk" SELECT * FROM "mdm"."organization_ou_crosswalk" LIMIT 1',
    'DELETE FROM "clm"."canonical_vendor_master"',
    'CREATE TABLE "clm"."reader_probe" (x int)',
    'DROP TABLE "finance"."ct_technology_financials"',
])
def test_reader_role_cannot_write(owner, config, statement):
    import psycopg

    with psycopg.connect(config.reader_dsn) as conn:
        conn.read_only = False  # prove the role, not the session flag, blocks the write
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement)
        conn.rollback()
