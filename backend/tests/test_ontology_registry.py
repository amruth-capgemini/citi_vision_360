"""Phase 1 gate: ontology YAML + registry.

Needs no Postgres, Neo4j, vector store or LLM. The data-pack conformance tests at the end run
only when the git-ignored synthetic data pack is present locally.
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest
import yaml

from citi_project.services.ontology import (
    InvalidRelationEndpointError,
    InvalidValueError,
    OntologyLoadError,
    OntologyRegistry,
    UnknownClassError,
    UnknownPropertyError,
    UnknownRelationError,
    default_ontology_dir,
)
from citi_project.services.ontology.models import DATATYPES

ONTOLOGY_DIR = default_ontology_dir()
REPO_ROOT = ONTOLOGY_DIR.parent.parent  # ontology/ is in backend/, initial_plan/ beside it


@pytest.fixture(scope="module")
def reg() -> OntologyRegistry:
    return OntologyRegistry.load()


# ------------------------------------------------------------------ fixtures


def minimal_modules() -> dict[str, dict]:
    """A small valid ontology used as the base for invalid-fixture mutations."""
    return {
        "core.yaml": {
            "module": "core",
            "description": "Core test module.",
            "classes": [
                {
                    "id": "Party",
                    "label": "Party",
                    "abstract": True,
                    "definition": "A party.",
                    "properties": [{"id": "name", "datatype": "string", "required": True, "description": "Name."}],
                },
                {
                    "id": "Doc",
                    "label": "Doc",
                    "kind": "document",
                    "key": "doc_id",
                    "definition": "A document.",
                    "properties": [
                        {"id": "doc_id", "datatype": "identifier", "required": True, "description": "Key."}
                    ],
                },
            ],
        },
        "commercial.yaml": {
            "module": "commercial",
            "description": "Commercial test module.",
            "classes": [
                {
                    "id": "Supplier",
                    "label": "Supplier",
                    "parent": "Party",
                    "key": "supplier_id",
                    "definition": "A supplier.",
                    "properties": [
                        {"id": "supplier_id", "datatype": "identifier", "required": True, "description": "Key."},
                        {"id": "tier", "datatype": "string", "enum": ["A", "B"], "description": "Tier."},
                    ],
                },
                {
                    "id": "Deal",
                    "label": "Deal",
                    "key": "deal_id",
                    "definition": "An agreement.",
                    "properties": [
                        {"id": "deal_id", "datatype": "identifier", "required": True, "description": "Key."}
                    ],
                },
            ],
            "relations": [
                {
                    "id": "PARTY_TO",
                    "label": "Party to",
                    "source": "Party",
                    "target": "Deal",
                    "cardinality": "many_to_many",
                    "description": "Party to a deal.",
                }
            ],
        },
    }


def write_ontology(root: Path, modules: dict[str, dict]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    shutil.copy(ONTOLOGY_DIR / "_schema.yaml", root / "_schema.yaml")
    for name, doc in modules.items():
        (root / name).write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return root


def load_error(tmp_path: Path, modules: dict[str, dict]) -> OntologyLoadError:
    with pytest.raises(OntologyLoadError) as exc:
        OntologyRegistry.load(write_ontology(tmp_path / "onto", modules))
    return exc.value


def cls(modules, file, class_id) -> dict:
    return next(c for c in modules[file]["classes"] if c["id"] == class_id)


# ------------------------------------------------------- real ontology shape


def test_real_ontology_loads_with_expected_vertical_slice(reg):
    for cid in ["Vendor", "Contract", "Clause", "RenewalClause", "TerminationClause", "PaymentTermsClause", "Invoice"]:
        assert cid in reg.classes
    for rid in ["PARTY_TO", "HAS_CLAUSE", "BILLED_UNDER"]:
        assert rid in reg.relations
    assert len(reg.ontology_version) == 64


def test_minimal_fixture_is_valid(tmp_path):
    reg = OntologyRegistry.load(write_ontology(tmp_path / "onto", minimal_modules()))
    assert set(reg.classes) == {"Party", "Doc", "Supplier", "Deal"}


def test_schema_datatypes_match_models():
    schema = yaml.safe_load((ONTOLOGY_DIR / "_schema.yaml").read_text(encoding="utf-8"))
    assert set(schema["$defs"]["property"]["properties"]["datatype"]["enum"]) == DATATYPES


# --------------------------------------------------------------- inheritance


def test_inherited_properties_and_ancestors(reg):
    vendor_props = reg.properties_for_class("Vendor")
    assert list(vendor_props)[0] == "legal_name"  # root ancestor's properties first
    assert "vendor_id" in vendor_props
    assert "legal_name" not in reg.properties_for_class("Vendor", include_inherited=False)
    assert reg.ancestors("RenewalClause") == ("Clause",)
    assert "section_title" in reg.properties_for_class("RenewalClause")
    assert reg.is_subclass_of("ContractDocument", "Document")
    assert not reg.is_subclass_of("Document", "ContractDocument")


def test_kind_and_key_resolve_through_inheritance(reg):
    doc = reg.get_class("ContractDocument")
    assert doc.kind == "document" and doc.key == "document_id"
    assert reg.get_class("RenewalClause").kind == "clause"
    assert reg.get_class("Vendor").kind == "entity"
    assert reg.get_class("InvoiceLine").kind == "fact"


def test_document_and_clause_filters(reg):
    assert reg.document_classes() == ["ContractDocument", "DependencyRegisterDocument", "SlaRiskPackDocument"]
    assert reg.clause_classes() == ["PaymentTermsClause", "RenewalClause", "TerminationClause"]
    assert "Clause" in reg.clause_classes(include_abstract=True)
    assert "Document" in reg.document_classes(include_abstract=True)


def test_abstract_class_cannot_have_instances(reg):
    reg.validate_class("Clause")
    with pytest.raises(UnknownClassError, match="abstract"):
        reg.validate_class("Clause", allow_abstract=False)


def test_unknown_class_with_synonym_hint(reg):
    with pytest.raises(UnknownClassError, match="Vendor"):
        reg.get_class("Supplier")
    assert reg.resolve_class("supplier").id == "Vendor"
    assert reg.resolve_class("SLA").id == "ServiceLevelAgreement"


# ----------------------------------------------------------------- relations


@pytest.mark.parametrize(
    "rel, src, tgt",
    [
        ("PARTY_TO", "Vendor", "Contract"),
        ("PARTY_TO", "Buyer", "Contract"),
        ("HAS_CLAUSE", "Contract", "RenewalClause"),  # subclass of declared target
        ("BILLED_UNDER", "Invoice", "Contract"),
        ("EVIDENCED_BY", "RenewalClause", "ContractDocument"),
        ("ASSESSES", "RiskAssessment", "Service"),
    ],
)
def test_allowed_relation_endpoints(reg, rel, src, tgt):
    assert reg.validate_relation(rel, src, tgt).id == rel


@pytest.mark.parametrize(
    "rel, src, tgt",
    [
        ("PARTY_TO", "Contract", "Vendor"),  # wrong direction
        ("HAS_CLAUSE", "Contract", "Vendor"),
        ("BILLED_UNDER", "InvoiceLine", "Contract"),
        ("SUPPORTS", "Vendor", "Application"),
    ],
)
def test_rejected_relation_endpoints(reg, rel, src, tgt):
    with pytest.raises(InvalidRelationEndpointError):
        reg.validate_relation(rel, src, tgt)


def test_unknown_relation_and_synonyms(reg):
    with pytest.raises(UnknownRelationError, match="PARTY_TO"):
        reg.get_relation("HAS_CONTRACT")
    assert reg.resolve_relation("HAS_CONTRACT").id == "PARTY_TO"
    with pytest.raises(UnknownRelationError):
        reg.resolve_relation("OPERATES")


def test_allowed_relations_between_classes(reg):
    assert [r.id for r in reg.allowed_relations("Service", "Application")] == ["SUPPORTS", "USES_PORTAL"]
    assert "HAS_CLAUSE" in [r.id for r in reg.allowed_relations(source_class="Contract")]
    assert reg.get_relation("USES_PORTAL").disjoint_with == ("SUPPORTS",)


# ---------------------------------------------------------- property values


@pytest.mark.parametrize(
    "class_id, prop, value",
    [
        ("Vendor", "vendor_type", "Technical"),
        ("Vendor", "aliases", ["Aurelix Codeworks", "SUP-1001"]),
        ("Contract", "end_date", "2026-12-27"),
        ("Contract", "annual_base_fee_usd_cents", 1000000000),
        ("InvoiceLine", "period", "2025-01"),
        ("PerformanceMeasurement", "actual", 99.7984),
        ("RiskAssessment", "risk_tier", None),  # optional: missing tier stays null
        ("RenewalClause", "automatic_renewal", False),
    ],
)
def test_valid_property_values(reg, class_id, prop, value):
    reg.validate_property(class_id, prop, value)


@pytest.mark.parametrize(
    "class_id, prop, value, match",
    [
        ("Vendor", "vendor_type", "Hardware", "not in allowed values"),
        ("Vendor", "status", None, "required value is null"),
        ("Vendor", "aliases", "single", "expects a list"),
        ("Vendor", "vendor_id", ["V-001"], "does not accept a list"),
        ("Vendor", "country", "USA", "pattern"),
        ("Contract", "end_date", "2026-02-30", "invalid calendar date"),
        ("Contract", "end_date", "27/12/2026", "ISO date"),
        ("Contract", "annual_base_fee_usd_cents", 10.5, "whole number"),
        ("Contract", "billable_assignments", True, "whole number"),
        ("InvoiceLine", "period", "2025-13", "YYYY-MM"),
        ("PerformanceMeasurement", "actual", 101, "outside 0-100"),
        ("RenewalClause", "automatic_renewal", "yes", "boolean"),
    ],
)
def test_invalid_property_values(reg, class_id, prop, value, match):
    with pytest.raises(InvalidValueError, match=match):
        reg.validate_property(class_id, prop, value)


def test_unknown_property_id(reg):
    with pytest.raises(UnknownPropertyError, match="Vendor"):
        reg.validate_property("Vendor", "risk_tier")


# ---------------------------------------------------------- extraction schema


def ev(quote="Notice is due 2026-10-28", page=4):
    return {"chunk_id": "V-001_contract_sow#c7", "page": page, "quote": quote}


def renewal_payload(**overrides):
    payload = {
        "section_title": None,
        "automatic_renewal": {"value": False, "confidence": 0.95, "evidence": ev("Renewal requires a new written approval.")},
        "renewal_extension_months": None,
        "notice_days": {"value": 60, "confidence": 0.9, "evidence": ev("the notice requirement is 60 calendar days")},
        "notice_deadline": {"value": "2026-10-28", "confidence": 0.9, "evidence": ev()},
        "renewal_mechanism": None,
    }
    payload.update(overrides)
    return payload


def test_extraction_schema_shape(reg):
    schema = reg.json_schema_for_extraction("RenewalClause")
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(reg.properties_for_class("RenewalClause"))
    assert schema["x-ontology-version"] == reg.ontology_version
    notice = schema["properties"]["notice_days"]["anyOf"]
    assert notice[0] == {"type": "null"}
    assert notice[1]["required"] == ["value", "confidence", "evidence"]


def test_valid_extraction_passes_with_nulls_without_evidence(reg):
    assert reg.validate_extraction("RenewalClause", renewal_payload()) == []


def test_extraction_docx_page_may_be_null(reg):
    payload = renewal_payload(notice_days={"value": 60, "confidence": 0.9, "evidence": ev(page=None)})
    assert reg.validate_extraction("RenewalClause", payload) == []


@pytest.mark.parametrize(
    "overrides, match",
    [
        ({"auto_renew_flag": None}, "Additional properties"),
        ({"notice_days": {"value": 60, "confidence": 0.9}}, "evidence"),
        ({"notice_days": {"value": 60, "confidence": 0.9, "evidence": {**ev(), "quote": ""}}}, "quote"),
        ({"notice_days": {"value": "60", "confidence": 0.9, "evidence": ev()}}, "notice_days"),
        ({"notice_days": {"value": 60, "confidence": 1.5, "evidence": ev()}}, "confidence"),
        ({"notice_deadline": {"value": "2026-02-30", "confidence": 0.9, "evidence": ev()}}, "invalid calendar date"),
    ],
)
def test_invalid_extraction_rejected(reg, overrides, match):
    errors = reg.validate_extraction("RenewalClause", renewal_payload(**overrides))
    assert errors and match in " ".join(errors)


def test_extraction_missing_key_and_enum(reg):
    payload = renewal_payload()
    del payload["notice_days"]
    assert any("notice_days" in e for e in reg.validate_extraction("RenewalClause", payload))
    schema_errors = reg.validate_extraction(
        "PaymentTermsClause",
        {
            "section_title": None,
            "payment_terms_days": {"value": 45, "confidence": 0.9, "evidence": ev()},
            "payment_schedule": None,
            "invoice_frequency": {"value": "Fortnightly", "confidence": 0.9, "evidence": ev()},
            "billing_currency": None,
        },
    )
    assert any("invoice_frequency" in e for e in schema_errors)


def test_extraction_schema_for_abstract_class_is_refused(reg):
    with pytest.raises(UnknownClassError):
        reg.json_schema_for_extraction("Clause")


# ------------------------------------------------------------ invalid fixtures


def test_duplicate_class_id_across_modules(tmp_path):
    mods = minimal_modules()
    mods["commercial.yaml"]["classes"].append(copy.deepcopy(cls(mods, "core.yaml", "Doc")))
    err = load_error(tmp_path, mods)
    msg = str(err)
    assert "commercial.yaml" in msg and "core.yaml" in msg and "Doc" in msg and "duplicate class ID" in msg


def test_duplicate_relation_id(tmp_path):
    mods = minimal_modules()
    mods["core.yaml"]["relations"] = copy.deepcopy(mods["commercial.yaml"]["relations"])
    assert "duplicate relation ID" in str(load_error(tmp_path, mods))


def test_unknown_parent(tmp_path):
    mods = minimal_modules()
    cls(mods, "commercial.yaml", "Supplier")["parent"] = "Organisation"
    err = load_error(tmp_path, mods)
    assert err.issues[0].offending_id == "Supplier" and "unknown parent 'Organisation'" in str(err)


def test_inheritance_cycle(tmp_path):
    mods = minimal_modules()
    cls(mods, "core.yaml", "Party")["parent"] = "Supplier"
    assert "inheritance cycle" in str(load_error(tmp_path, mods))


def test_unsupported_datatype_names_file_and_ids(tmp_path):
    mods = minimal_modules()
    cls(mods, "commercial.yaml", "Supplier")["properties"][1]["datatype"] = "float"
    issue = load_error(tmp_path, mods).issues[0]
    assert issue.file == "commercial.yaml" and issue.module == "commercial"
    assert issue.offending_id == "Supplier.tier" and "float" in issue.message


@pytest.mark.parametrize("where", ["property", "relation"])
def test_invalid_cardinality(tmp_path, where):
    mods = minimal_modules()
    if where == "property":
        cls(mods, "commercial.yaml", "Supplier")["properties"][1]["cardinality"] = "several"
    else:
        mods["commercial.yaml"]["relations"][0]["cardinality"] = "many"
    assert "cardinality" in str(load_error(tmp_path, mods))


def test_enum_on_non_string_datatype(tmp_path):
    mods = minimal_modules()
    cls(mods, "commercial.yaml", "Supplier")["properties"][1]["datatype"] = "integer"
    assert "enum not allowed for datatype 'integer'" in str(load_error(tmp_path, mods))


def test_duplicate_enum_values(tmp_path):
    mods = minimal_modules()
    cls(mods, "commercial.yaml", "Supplier")["properties"][1]["enum"] = ["A", "A"]
    assert "enum values must be unique" in str(load_error(tmp_path, mods))


def test_relation_endpoint_unknown_class(tmp_path):
    mods = minimal_modules()
    mods["commercial.yaml"]["relations"][0]["target"] = ["Deal", "Invoice"]
    err = load_error(tmp_path, mods)
    assert err.issues[0].offending_id == "PARTY_TO" and "'Invoice'" in err.issues[0].message


def test_redefining_inherited_property(tmp_path):
    mods = minimal_modules()
    cls(mods, "commercial.yaml", "Supplier")["properties"].append(
        {"id": "name", "datatype": "text", "description": "Shadowing name."}
    )
    assert "redefines property inherited from Party" in str(load_error(tmp_path, mods))


def test_kind_conflict_with_parent(tmp_path):
    mods = minimal_modules()
    cls(mods, "commercial.yaml", "Supplier")["kind"] = "document"
    assert "conflicts with inherited kind" in str(load_error(tmp_path, mods))


def test_missing_key_property(tmp_path):
    mods = minimal_modules()
    cls(mods, "commercial.yaml", "Deal")["key"] = "contract_ref"
    assert "key 'contract_ref' is not a property" in str(load_error(tmp_path, mods))
    del cls(mods, "commercial.yaml", "Deal")["key"]
    assert "concrete class has no key property" in str(load_error(tmp_path, mods))


def test_duplicate_module_name(tmp_path):
    mods = minimal_modules()
    mods["commercial.yaml"]["module"] = "core"
    assert "module name also declared" in str(load_error(tmp_path, mods))


def test_asymmetric_disjoint_and_synonym_collision(tmp_path):
    mods = minimal_modules()
    rel = mods["commercial.yaml"]["relations"][0]
    mods["commercial.yaml"]["relations"].append({**copy.deepcopy(rel), "id": "SIGNED", "disjoint_with": ["PARTY_TO"]})
    cls(mods, "commercial.yaml", "Deal")["synonyms"] = ["supplier"]
    msg = str(load_error(tmp_path, mods))
    assert "not declared symmetrically" in msg and "collides with class ID 'Supplier'" in msg


def test_unknown_top_level_key_rejected(tmp_path):
    mods = minimal_modules()
    cls(mods, "core.yaml", "Doc")["colour"] = "blue"
    assert "colour" in str(load_error(tmp_path, mods))


def test_all_issues_reported_together(tmp_path):
    mods = minimal_modules()
    cls(mods, "commercial.yaml", "Supplier")["parent"] = "Nope"
    mods["commercial.yaml"]["classes"].append(copy.deepcopy(cls(mods, "core.yaml", "Doc")))
    assert len(load_error(tmp_path, mods).issues) == 2


# ------------------------------------------------------------ version hashing


def _scramble(node):
    """Reverse every list and mapping order recursively (semantically irrelevant here)."""
    if isinstance(node, dict):
        return {k: _scramble(node[k]) for k in reversed(list(node))}
    if isinstance(node, list):
        return [_scramble(v) for v in reversed(node)]
    return node


def _real_modules() -> dict[str, dict]:
    return {
        p.name: yaml.safe_load(p.read_text(encoding="utf-8"))
        for p in sorted(ONTOLOGY_DIR.glob("*.yaml"))
        if not p.name.startswith("_")
    }


def test_version_stable_across_formatting_order_and_file_layout(reg, tmp_path):
    mods = {name: _scramble(doc) for name, doc in _real_modules().items()}
    # move a class and a relation to a different file
    moved_cls = mods["commercial.yaml"]["classes"].pop()
    moved_rel = mods["commercial.yaml"]["relations"].pop()
    mods["core.yaml"]["classes"].append(moved_cls)
    mods["core.yaml"]["relations"].append(moved_rel)
    root = tmp_path / "onto"
    root.mkdir()
    shutil.copy(ONTOLOGY_DIR / "_schema.yaml", root / "_schema.yaml")
    for i, (name, doc) in enumerate(reversed(list(mods.items()))):
        # different filenames, flow style and line width
        text = yaml.safe_dump(doc, default_flow_style=bool(i % 2), width=60, sort_keys=False)
        (root / f"m{i}_{name}").write_text(text, encoding="utf-8")
    assert OntologyRegistry.load(root).ontology_version == reg.ontology_version


def test_version_changes_when_meaning_changes(reg, tmp_path):
    mods = _real_modules()
    cls(mods, "contracts.yaml", "RenewalClause")["definition"] += " Amended."
    changed = OntologyRegistry.load(write_ontology(tmp_path / "a", mods)).ontology_version
    assert changed != reg.ontology_version

    mods = _real_modules()
    cls(mods, "commercial.yaml", "Vendor")["properties"][1]["enum"].append("Hybrid")
    assert OntologyRegistry.load(write_ontology(tmp_path / "b", mods)).ontology_version != reg.ontology_version


def test_explicit_defaults_do_not_change_version(reg, tmp_path):
    mods = _real_modules()
    vendor = cls(mods, "commercial.yaml", "Vendor")
    vendor["kind"] = "entity"
    vendor["abstract"] = False
    for prop in vendor["properties"]:
        prop.setdefault("required", False)
        prop.setdefault("cardinality", "one")
    assert OntologyRegistry.load(write_ontology(tmp_path / "onto", mods)).ontology_version == reg.ontology_version


# ------------------------------------------------- synthetic data pack conformance

_PACKS = sorted((REPO_ROOT / "initial_plan").glob("Synthetic_20_Vendor*/data"))
DATA = _PACKS[0] if _PACKS else None
needs_data = pytest.mark.skipif(DATA is None, reason="synthetic data pack not present (git-ignored)")


def _json(rel: str):
    return json.loads((DATA / rel).read_text(encoding="utf-8"))


def _vendor_files(pattern: str):
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(DATA.glob(pattern))]


def _classify_one(reg, raw_id: str) -> str:
    matches = reg.classify_identifier(raw_id)
    assert len(matches) == 1, f"{raw_id!r} classifies to {matches}"
    return matches[0]


@needs_data
def test_every_source_relationship_maps_to_an_allowed_ontology_relation(reg):
    rows = [r for d in _vendor_files("05_service_dependencies/V-*_dependencies.json") for r in d["relationships"]]
    rows += _json("05_service_dependencies/reference_relationships.json")["records"]
    assert len(rows) == 374  # validation_report.json: relationships
    for row in rows:
        rel = reg.resolve_relation(row["relationship"])
        reg.validate_relation(rel.id, _classify_one(reg, row["from_id"]), _classify_one(reg, row["to_id"]))
        if "dependency_type" in row:
            # dependency_type is implied by the relation ID (see technology.yaml)
            assert (row["dependency_type"] == "Enabling only") == (rel.id == "USES_PORTAL"), row


@needs_data
def test_potential_alternative_status_conforms(reg):
    rel = reg.get_relation("POTENTIAL_ALTERNATIVE_TO")
    status = next(p for p in rel.properties if "alternative_capacity_status" in p.synonyms)
    for dep in _vendor_files("05_service_dependencies/V-*_dependencies.json"):
        assert dep["alternative_capacity_status"] in status.enum
        if dep["potential_alternative_vendor_id"]:
            reg.validate_relation(rel.id, _classify_one(reg, dep["potential_alternative_vendor_id"]), "Vendor")


@needs_data
def test_uses_portal_never_counts_as_support(reg):
    dep = _json("05_service_dependencies/V-013_dependencies.json")
    rels = {r["relationship"] for r in dep["relationships"]}
    assert "USES_PORTAL" in rels and "SUPPORTS" not in rels
    assert "SUPPORTS" in reg.get_relation("USES_PORTAL").disjoint_with


# fields that are foreign keys (become relations), provenance, entitlement or deferred structures
_NON_PROPERTY_FIELDS = {
    "Vendor": {"parent_vendor_id", "buyer", "primary_owner_id", "organization_id", "service_id", "contract_id", "as_of", "synthetic"},
    "Application": {"product_id", "owner_id", "synthetic"},
    "RiskAssessment": {"vendor_id", "assessed_entity_id", "risk_owner_id", "source_document_id", "synthetic"},
    "RiskIssue": {"vendor_id", "assessment_id", "affected_entity_id", "owner_id", "synthetic"},
    "PerformanceMeasurement": {"vendor_id", "contract_id", "service_id", "source_document_id", "synthetic"},
    "ServiceLevelAgreement": {"vendor_id", "service_id", "credit_bands", "synthetic"},
    "Assignment": {"vendor_id", "contract_id", "sow_id", "service_id", "application_id", "record_as_of", "acl_roles", "synthetic"},
    "InvoiceLine": {"invoice_id", "vendor_id", "source_vendor_code", "contract_id", "service_id", "cost_center_id",
                    "organization_id", "product_id", "status", "source_as_of", "synthetic"},
}


def _flatten(record: dict, nested: str | None = None, prefix_map=None) -> dict:
    out = {}
    for k, v in record.items():
        if isinstance(v, dict) and k == nested:
            for sub_k, sub_v in v.items():
                out[prefix_map(sub_k) if prefix_map else f"{k}.{sub_k}"] = sub_v
        else:
            out[k] = v
    return out


def _check_records(reg, class_id: str, records: list[dict]):
    assert records
    for rec in records:
        mapped = {k: v for k, v in rec.items() if reg.property_for_field(class_id, k) is not None}
        unmapped = set(rec) - set(mapped)
        assert unmapped <= _NON_PROPERTY_FIELDS[class_id], f"{class_id}: unmodelled fields {unmapped - _NON_PROPERTY_FIELDS[class_id]}"
        assert reg.validate_record(class_id, mapped) == [], rec


@needs_data
def test_source_records_conform_to_ontology(reg):
    vendors = [
        _flatten(r, "source_ids", lambda s: f"{s.lower()}_source_id")
        for d in _vendor_files("01_vendor_ownership/V-*_vendor.json")
        for r in d["records"]
    ]
    risk = _vendor_files("06_risk_sla_performance/V-*_risk_sla.json")
    _check_records(reg, "Vendor", vendors)
    _check_records(reg, "Application", _json("05_service_dependencies/application_catalog.json")["records"])
    _check_records(reg, "RiskAssessment", [d["risk_assessment"] for d in risk])
    _check_records(reg, "RiskIssue", [i for d in risk for i in d["issues"]])
    _check_records(reg, "PerformanceMeasurement", [m for d in risk for m in d["performance"]])
    _check_records(reg, "ServiceLevelAgreement", [d["sla"] for d in risk])
    _check_records(reg, "Assignment", [a for d in _vendor_files("04_workforce/V-*_workforce.json") for a in d["assignments"]])
    _check_records(
        reg,
        "InvoiceLine",
        [_flatten(r, "variance_drivers_usd_cents") for d in _vendor_files("03_spend_forecast/V-*_finance.json") for r in d["records"]],
    )


_CONTRACT_NON_PROPERTY = {
    "sow_id", "vendor_id", "service_id", "buyer", "owner_id", "source_document_id", "synthetic",
    "days_to_expiry",  # derived from end_date and the as-of date
    "clause_refs",  # evidence locations, not properties
    "deliverables",  # Deliverable instances
    "milestones", "rate_card", "sprints", "entitlements", "unit_rate_card",  # deferred pricing structures
}
_CONTRACT_TARGETS = ["Contract", "RenewalClause", "TerminationClause", "PaymentTermsClause"]


@needs_data
def test_contract_records_split_into_contract_and_clauses(reg):
    for doc in _vendor_files("02_contracts_sows/V-*_contract.json"):
        rec = doc["records"][0]
        parts: dict[str, dict] = {c: {} for c in _CONTRACT_TARGETS}
        for field, value in rec.items():
            owners = [c for c in _CONTRACT_TARGETS if reg.property_for_field(c, field) is not None]
            if field in _CONTRACT_NON_PROPERTY:
                assert not owners, f"{field} unexpectedly modelled on {owners}"
                continue
            assert len(owners) == 1, f"{rec['contract_id']}.{field} maps to {owners}"
            parts[owners[0]][field] = value
        for class_id, subset in parts.items():
            assert reg.validate_record(class_id, subset) == [], (rec["contract_id"], class_id)
        for d in rec["deliverables"]:
            assert reg.validate_record("Deliverable", d, allow_unmapped=True) == []


@needs_data
def test_missing_assessment_keeps_null_tier(reg):
    ra = _json("06_risk_sla_performance/V-017_risk_sla.json")["risk_assessment"]
    assert ra["status"] == "Missing" and ra["risk_tier"] is None
    reg.validate_property("RiskAssessment", "risk_tier", None)
    assert not reg.get_property("RiskAssessment", "risk_tier").required
