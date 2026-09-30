from dataclasses import replace

import pytest

from citi_project.services.ontology import OntologyRegistry
from citi_project.services.knowledge_graph import (
    EntityInstance, EntityRef, Evidence, GraphInputError, GraphMapper, GraphPayload, RelationshipInstance,
)
from citi_project.services.knowledge_graph.identity import canonical_identity


@pytest.fixture
def mapper():
    return GraphMapper(OntologyRegistry.load())


def renewal(occurrence="renewal-1", contract="CTR-001", **kwargs):
    return EntityInstance("RenewalClause", {"automatic_renewal": True, "notice_days": 60, **kwargs},
                          contract_identity=contract, occurrence_id=occurrence)


def test_clause_occurrence_identity(mapper):
    original = renewal(renewal_mechanism="Original text")
    first = mapper.node("test", original)
    assert first.key != mapper.node("test", renewal("renewal-2")).key
    assert first == mapper.node("test", original)
    updated = replace(original, revision=2, properties={**original.properties, "renewal_mechanism": "Updated text"})
    assert first.key == mapper.node("test", updated).key
    assert first.key != mapper.node("test", renewal(contract="CTR-002")).key
    assert first.key != mapper.node("other", original).key


@pytest.mark.parametrize("class_id,properties", [
    ("RenewalClause", {"automatic_renewal": True, "notice_days": 60}),
    ("TerminationClause", {}), ("PaymentTermsClause", {"payment_terms_days": 30}),
])
def test_missing_clause_occurrence_rejected(mapper, class_id, properties):
    with pytest.raises(GraphInputError, match="occurrence_id"):
        mapper.node("test", EntityInstance(class_id, properties, contract_identity="CTR-001"))


def test_inheritance_keys_synonyms_and_separation(mapper):
    vendor = EntityInstance("Vendor", {"vendor_id": "V-001", "name": "Example", "status": "Active", "vendor_type": "Technical"})
    row = mapper.node("test", vendor)
    assert row.labels == ("CitiKGEntity", "Vendor", "LegalEntity")
    assert row.properties["legal_name"] == "Example"
    assert "name" not in row.properties
    assert canonical_identity(mapper.registry, "test", EntityRef("Contract", "CTR-001")) != canonical_identity(
        mapper.registry, "test", EntityRef("ContractDocument", "V-001_contract_sow"))


@pytest.mark.parametrize("entity", [
    EntityInstance("NotAClass", {}), EntityInstance("Clause", {}),
    EntityInstance("Service", {"service_id": "SVC-001"}),
    EntityInstance("Service", {"service_id": "bad", "service_name": "Name"}),
    EntityInstance("Service", {"service_id": "SVC-001", "service_name": "Name"}, identity="SVC-002"),
    EntityInstance("Service", {"service_id": "SVC-001", "service_name": "Name", "extra": "bad"}),
    EntityInstance("Assignment", {"assignment_id": "ASN-001-001", "role": "Role", "billable": True, "fte": float("nan")}),
])
def test_invalid_nodes(mapper, entity):
    with pytest.raises(GraphInputError):
        mapper.node("test", entity)


def test_alias_conflict_rejected(mapper):
    with pytest.raises(GraphInputError, match="Multiple"):
        mapper.node("test", EntityInstance("Buyer", {"legal_name": "A", "name": "B"}))


def test_relationship_mapping_and_properties(mapper):
    rel = RelationshipInstance("HAS_CONTRACT", EntityRef("Vendor", "V-001"), EntityRef("Contract", "CTR-001"))
    assert mapper.edge("test", rel).type == "PARTY_TO"
    with pytest.raises(GraphInputError):
        mapper.edge("test", replace(rel, source=rel.target, target=rel.source))
    with pytest.raises(GraphInputError):
        mapper.edge("test", replace(rel, type="INVENTED"))
    alternative = RelationshipInstance("POTENTIAL_ALTERNATIVE_TO", EntityRef("Vendor", "V-001"), EntityRef("Vendor", "V-002"))
    with pytest.raises(GraphInputError):
        mapper.edge("test", alternative)
    row = mapper.edge("test", replace(alternative, properties={"alternative_capacity_status": "Unvalidated"}))
    assert row.properties["validation_status"] == "Unvalidated"
    with pytest.raises(GraphInputError):
        mapper.edge("test", replace(alternative, properties={"validation_status": "invented"}))


def test_all_declared_relationship_endpoints_map(mapper):
    # Endpoint validation is shared with the registry, including inherited endpoints.
    for relation in mapper.registry.relations.values():
        for source in relation.source:
            for target in relation.target:
                assert mapper.registry.validate_relation(relation.id, source, target) == relation


def test_provenance_is_technical_and_validated(mapper):
    doc = EntityRef("ContractDocument", "V-001_contract_sow")
    evidence = Evidence(document=doc, property_id="notice_days", page=4, chunk_id="chunk-1", confidence=0.9)
    row = mapper.node("test", replace(renewal(), provenance=(evidence, evidence)))
    assert "_kg_provenance" in row.properties and "page" not in row.properties
    assert canonical_identity(mapper.registry, "test", doc) in row.references
    assert row == mapper.node("test", replace(renewal(), provenance=(evidence,)))
    for bad in (replace(evidence, confidence=2), replace(evidence, page=True),
                replace(evidence, document=None), replace(evidence, property_id="unknown")):
        with pytest.raises(GraphInputError):
            mapper.node("test", replace(renewal(), provenance=(bad,)))


def test_payload_dedup_and_conflict(mapper):
    payload = GraphPayload("test", mapper.registry.ontology_version, (renewal(), renewal()))
    assert len(mapper.map(payload).nodes) == 1
    with pytest.raises(GraphInputError, match="Conflicting"):
        mapper.map(replace(payload, entities=(renewal(), renewal(notice_days=30))))
    with pytest.raises(GraphInputError, match="ontology_version"):
        mapper.map(replace(payload, ontology_version="wrong"))


def test_strict_json_contract(mapper):
    value = {"namespace": "test", "ontology_version": mapper.registry.ontology_version,
             "entities": [{"class_id": "Service", "properties": {"service_id": "SVC-001", "service_name": "Service"}}]}
    assert len(mapper.map(GraphPayload.from_dict(value)).nodes) == 1
    with pytest.raises(GraphInputError):
        GraphPayload.from_dict({**value, "unknown": 1})
    with pytest.raises(GraphInputError):
        GraphPayload.from_dict({**value, "entities": [{}]})


def test_every_concrete_class_maps_from_registry(mapper):
    examples = ["V-001", "PARENT-01", "ORG-01", "PROD-01", "OWN-001", "SITE-01", "CTR-001", "SOW-001",
                "SOW-001-D1", "V-001_contract_sow", "SVC-001", "APP-001", "CI-001-1", "PROC-001",
                "V-001_dependency_register", "SLA-001", "MET-001-01", "RA-001", "ISSUE-001", "V-001_sla_risk_pack",
                "ASN-001-001", "INV-001-202501", "INV-001-202501-01", "PO-001-2026", "CC-01", "SYN-ERP"]
    values = {"string": "example", "text": "example", "identifier": "example", "integer": 1,
              "money_cents": 100, "decimal": 1.5, "percentage": 99.5, "boolean": True,
              "date": "2026-01-01", "year_month": "2026-01"}
    mapped = {}
    for cid, cls in mapper.registry.classes.items():
        if cls.abstract:
            continue
        properties = {}
        for p in mapper.registry.properties_for_class(cid).values():
            if p.required:
                value = p.enum[0] if p.enum else values[p.datatype]
                properties[p.id] = [value] if p.cardinality == "many" else value
        if cls.id_pattern:
            properties[cls.key] = next(v for v in examples if cid in mapper.registry.classify_identifier(v))
        kwargs = {"contract_identity": "CTR-001", "occurrence_id": "first"} if cls.kind == "clause" else {}
        row = mapper.node("test", EntityInstance(cid, properties, **kwargs))
        assert cid in row.labels
        assert set(mapper.registry.ancestors(cid)) <= set(row.labels)
        mapped[cid] = EntityRef(cid, row.properties.get("_kg_identity"), row.properties.get("_kg_contract_identity"),
                                row.properties.get("_kg_occurrence_id"))
    assert len(mapped) == 33
    for rid, rel in mapper.registry.relations.items():
        source = next(ref for cid, ref in mapped.items() if any(mapper.registry.is_subclass_of(cid, s) for s in rel.source))
        target = next(ref for cid, ref in mapped.items() if any(mapper.registry.is_subclass_of(cid, t) for t in rel.target))
        properties = {p.id: p.enum[0] if p.enum else values[p.datatype] for p in rel.properties if p.required}
        assert mapper.edge("test", RelationshipInstance(rid, source, target, properties)).type == rid
