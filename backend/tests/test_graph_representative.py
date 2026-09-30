"""Phase 1 is entirely offline: real mapper/topology, optional in-memory writer double."""

from collections import Counter, defaultdict
from dataclasses import replace
import json
from pathlib import Path
import runpy
from unittest.mock import patch

import pytest

from citi_project.services.ontology import OntologyRegistry
from citi_project.services.knowledge_graph import EntityRef, GraphInputError, GraphMapper, GraphPayload
from citi_project.services.knowledge_graph.identity import canonical_identity
from citi_project.services.knowledge_graph.validation import topology_report

FIXTURES = Path(__file__).parent / "fixtures/knowledge_graph"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture
def graph():
    registry = OntologyRegistry.load()
    payload = GraphPayload.from_dict(json.loads((FIXTURES / "representative.json").read_text(encoding="utf-8")))
    mapper = GraphMapper(registry)
    return registry, payload, mapper, mapper.map(payload)


def test_payload_mapping_topology_and_exact_counts(graph):
    registry, payload, _, mapped = graph
    assert payload.namespace == MANIFEST["namespace"]
    assert len(payload.entities) == len(mapped.nodes) == MANIFEST["node_count"]
    assert len(payload.relationships) == len(mapped.edges) == MANIFEST["relationship_count"]
    assert Counter(n.class_id for n in mapped.nodes) == MANIFEST["nodes_by_class"]
    assert Counter(e.type for e in mapped.edges) == MANIFEST["relationships_by_type"]
    assert topology_report(registry, mapped.nodes, mapped.edges).valid


def test_unique_keys_and_distinct_same_contract_clauses(graph):
    _, _, _, mapped = graph
    assert len({n.key for n in mapped.nodes}) == len(mapped.nodes)
    assert len({e.key for e in mapped.edges}) == len(mapped.edges)
    clauses = [n for n in mapped.nodes if n.class_id == "RenewalClause" and n.properties["_kg_contract_identity"] == "CTR-001"]
    assert len(clauses) == 2 and len({n.key for n in clauses}) == 2
    assert {n.properties["_kg_occurrence_id"] for n in clauses} == {"renew1", "renew-support-extension"}


def test_replay_reordering_and_duplicate_rows(graph):
    _, payload, mapper, mapped = graph
    assert mapper.map(payload) == mapped
    assert mapper.map(replace(payload, entities=tuple(reversed(payload.entities)),
                              relationships=tuple(reversed(payload.relationships)))) == mapped
    duplicate = mapper.map(replace(payload, entities=payload.entities * 2, relationships=payload.relationships * 2))
    assert duplicate.nodes == mapped.nodes and duplicate.edges == mapped.edges
    assert duplicate.input_entities == 94 and duplicate.input_relationships == 140


@pytest.mark.parametrize("case", ["reversed", "invalid_endpoint"])
def test_invalid_relationship_rejected(graph, case):
    _, payload, mapper, _ = graph
    relation = next(e for e in payload.relationships if e.type == "PROVIDES")
    invalid = replace(relation, source=relation.target, target=relation.source) if case == "reversed" else replace(
        relation, target=EntityRef("Contract", "CTR-001"))
    with pytest.raises(GraphInputError, match="directed endpoints"):
        mapper.map(replace(payload, relationships=(*payload.relationships, invalid)))


def test_revision_update_retains_identity_and_only_expected_changes(graph):
    _, payload, mapper, mapped = graph
    original = next(e for e in payload.entities if e.occurrence_id == "renew1")
    updated = replace(original, revision=2, properties={**original.properties, "notice_days": 45})
    result = mapper.map(replace(payload, entities=tuple(updated if e == original else e for e in payload.entities)))
    before, after = {n.key: n for n in mapped.nodes}, {n.key: n for n in result.nodes}
    assert before.keys() == after.keys()
    changed = [k for k in before if before[k] != after[k]]
    assert len(changed) == 1
    old, new = before[changed[0]], after[changed[0]]
    assert new.properties == {**old.properties, "notice_days": 45, "_kg_revision": 2}
    assert new.references == old.references and new.labels == old.labels
    assert result.edges == mapped.edges


def test_provenance_resolves_documents_and_retained_records(graph):
    registry, payload, _, mapped = graph
    documents = {n.key: n for n in mapped.nodes if registry.get_class(n.class_id).kind == "document"}
    records = json.loads((FIXTURES / "evidence.json").read_text(encoding="utf-8"))
    used = set()
    for instance in (*payload.entities, *payload.relationships):
        assert instance.provenance
        for citation in instance.provenance:
            if citation.document is None:
                assert citation.source_ref == "synthetic:reference-register-v1"
                continue
            key = canonical_identity(registry, payload.namespace, citation.document)
            assert key in documents
            assert 1 <= citation.page <= documents[key].properties["pages_expected"]
            record = records[citation.evidence_ref]
            assert record["document_id"] == citation.document.identity
            assert record["page"] == citation.page and record["chunk_id"] == citation.chunk_id
            assert record["section"] == citation.section and record["synthetic"] is True
            if hasattr(instance, "class_id"):
                assert record["assertion"] == instance.properties
            else:
                assert record["assertion"]["type"] == instance.type
                assert EntityRef(**record["assertion"]["source"]) == instance.source
                assert EntityRef(**record["assertion"]["target"]) == instance.target
            used.add(citation.evidence_ref)
    assert used == set(records)


@pytest.mark.parametrize("path", MANIFEST["paths"], ids=lambda p: p["name"])
def test_expected_multihop_paths(graph, path):
    registry, payload, _, mapped = graph
    def key(ref):
        return canonical_identity(registry, payload.namespace, EntityRef(**ref))
    frontier = {key(path["start"])}
    for relation, direction in path["steps"]:
        assert direction in ("out", "in")
        frontier = {e.target if direction == "out" else e.source for e in mapped.edges
                    if e.type == relation and (e.source if direction == "out" else e.target) in frontier}
    assert frontier == {key(ref) for ref in path["expected"]}


def test_one_connected_component_and_business_consistency(graph):
    _, _, _, mapped = graph
    neighbors = defaultdict(set)
    for edge in mapped.edges:
        neighbors[edge.source].add(edge.target)
        neighbors[edge.target].add(edge.source)
    visited, pending = set(), [mapped.nodes[0].key]
    while pending:
        key = pending.pop()
        if key not in visited:
            visited.add(key)
            pending.extend(neighbors[key] - visited)
    assert visited == {n.key for n in mapped.nodes}
    lines = [n.properties for n in mapped.nodes if n.class_id == "InvoiceLine"]
    totals = MANIFEST["business_totals"]
    assert sum(p["actual_usd_cents"] for p in lines) == totals["posted_actual_usd_cents"]
    assert sum(p["forecast_usd_cents"] for p in lines) == totals["forecast_usd_cents"]
    assert sum(p["variance_usd_cents"] for p in lines) == totals["variance_usd_cents"]
    for p in lines:
        assert p["period"] == "2025-12"
        assert p["variance_usd_cents"] == p["actual_usd_cents"] - p["forecast_usd_cents"]
        assert p["variance_usd_cents"] == sum(p[k] for k in ("volume_change_usd_cents", "rate_mix_usd_cents", "scope_change_usd_cents"))
    assert sum(n.properties["open_commitment_usd_cents"] for n in mapped.nodes if n.class_id == "PurchaseCommitment") == totals["forward_commitment_usd_cents"]
    for n in mapped.nodes:
        p = n.properties
        if n.class_id == "PerformanceMeasurement":
            assert p["actual"] == pytest.approx(100 * (p["eligible_units"] - p["failed_units"]) / p["eligible_units"])
            assert p["breach"] == (p["actual"] < p["target"])
        if n.class_id == "RiskAssessment" and p["status"] == "Missing":
            assert "risk_tier" not in p and "assessment_date" not in p


def test_fixture_builder_is_reproducible(tmp_path):
    builder = runpy.run_path(str(FIXTURES / "build_fixture.py"))
    builder["build"](tmp_path)
    for name in ("representative.json", "evidence.json"):
        assert (tmp_path / name).read_bytes() == (FIXTURES / name).read_bytes()


def test_existing_offline_cli_accepts_fixture(capsys):
    from citi_project.services.knowledge_graph.cli import main
    with patch("citi_project.services.knowledge_graph.cli.create_client") as connect:
        assert main(["ingest", str(FIXTURES / "representative.json"), "--dry-run"]) == 0
        connect.assert_not_called()
    result = json.loads(capsys.readouterr().out)
    assert result["entities"] == 47 and result["relationships"] == 70
    assert result["database_checked"] is False


def test_existing_service_replay_and_revision_offline(graph):
    from test_graph_service import MemoryClient
    from citi_project.services.knowledge_graph.service import KnowledgeGraphService
    registry, payload, _, _ = graph
    service = KnowledgeGraphService(registry, MemoryClient(registry), batch_size=5)
    first = service.ingest(payload)
    second = service.ingest(payload)
    assert (first.written_entities, first.written_relationships) == (47, 70)
    assert second.written_entities == second.written_relationships == 0
    assert service.validate(payload.namespace, payload, exact=True).valid
    clause = next(e for e in payload.entities if e.occurrence_id == "renew1")
    updated = replace(clause, revision=2, properties={**clause.properties, "notice_days": 45})
    result = service.upsert_entity(payload.namespace, updated)
    assert result.written_entities == 1 and result.written_relationships == 0
    expected = replace(payload, entities=tuple(updated if e == clause else e for e in payload.entities))
    assert service.validate(payload.namespace, expected, exact=True).valid
    with pytest.raises(GraphInputError, match="newer revision"):
        service.upsert_entity(payload.namespace, clause)
