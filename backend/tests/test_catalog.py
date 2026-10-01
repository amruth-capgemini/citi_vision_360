"""Offline metadata-catalog tests over an in-memory copy of the loaded Postgres tables."""

import csv
from dataclasses import asdict, replace
import json
from pathlib import Path
import re

import pytest

from citi_project.services.agents.contracts import ModelError
from citi_project.services.catalog import (CATALOG_NAMESPACE, CatalogEnricher, CatalogQueryService, Classifier,
                                           PostgresCatalogSource, build_payload, harvest,
                                           load_catalog_registry, summarize)
from citi_project.services.catalog import cli as catalog_cli
from citi_project.services.catalog.classifier import concept_name, humanize
from citi_project.services.catalog.harvester import HarvestError, value_pattern
from citi_project.services.catalog.relationships import discover_references
from citi_project.services.knowledge_graph.mapping import GraphMapper
from citi_project.services.knowledge_graph.models import GraphInputError
from citi_project.services.knowledge_graph.validation import topology_report
from citi_project.services.ontology import OntologyRegistry
from citi_project.services.postgres.tables import SYSTEMS, TABLES

DATA = Path(__file__).resolve().parents[2] / "initial_plan"
NAMESPACE = "synthetic-pack-20260928"


class MemorySource:
    """The harvest source interface over CSV rows, with the SQL source's semantics ('' is empty)."""

    def __init__(self, drop=None):
        self.tables_ = {}
        for spec in TABLES.values():
            with (DATA / spec.source_file).open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                rows = [r for r in reader if not (drop and drop(spec.name, r))]
            self.tables_[(spec.schema, spec.table)] = (spec, reader.fieldnames, rows)

    def schemas(self, names):
        return {n: SYSTEMS[n] for n in names if n in SYSTEMS}

    def tables(self, schema):
        return sorted((t, f"comment for {t}") for (s, t) in self.tables_ if s == schema)

    def columns(self, schema, table):
        _, fields, _ = self.tables_[(schema, table)]
        return [(n, "text", i, None) for i, n in enumerate([*fields, "_source_line", "_source_file"], 1)]

    def primary_key(self, schema, table):
        return list(self.tables_[(schema, table)][0].key)

    def stats(self, schema, table, names):
        rows = self.tables_[(schema, table)][2]
        stats = []
        for name in names:
            values = [r[name] for r in rows if r[name]]
            numeric = all(re.fullmatch(r"-?[0-9]+(\.[0-9]+)?", v) for v in values)
            dates = all(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", v) for v in values)
            stats.append((len(rows) - len(values), len(set(values)), min(values, default=None), max(values, default=None),
                          max((len(r[name]) for r in rows), default=0), numeric, dates))
        return len(rows), stats

    def distinct(self, schema, table, name, limit, scoped, namespace):
        rows = self.tables_[(schema, table)][2]
        return sorted({r[name] for r in rows if r[name] and (not scoped or r[scoped] == namespace)})[:limit]

    def sample(self, schema, table, names, limit):
        return [{n: r[n] for n in names} for r in self.tables_[(schema, table)][2][:limit]]

    def anchors(self, namespace):
        rows = self.tables_[("clm", "canonical_vendor_master")][2]
        return [{"vendor_id": r["Vendor_ID"], "vendor_name": r["Vendor_Name"], "contract_id": r["Contract_ID"],
                 "contract_description": r["Contract_Description"]} for r in rows if r["Graph_Namespace"] == namespace]


class FakeModel:
    """Returns scripted proposals for any requested columns; records every request."""

    def __init__(self, mapping=None, fail=(), confidence=0.9):
        self.mapping, self.fail, self.confidence, self.requests = mapping or {}, set(fail), confidence, []

    def complete(self, stage, prompt, payload, schema):
        self.requests.append((stage, payload, schema))
        if payload["dataset"] in self.fail:
            raise ModelError("Azure OpenAI request failed: timeout")
        output = {"columns": [{"name": n, "business_name": f"Name of {n}", "description": f"Business meaning of {n}.",
                               "ontology_mapping": self.mapping.get(n, "none"), "confidence": self.confidence}
                              for n in payload["describe_columns"]]}
        if "summary" in schema["properties"]:
            output = {"summary": f"Summary of {payload['dataset']}.", "grain": "one row per contract x scenario",
                      "domains": ["spend_forecast"], **output}
        return output


@pytest.fixture(scope="module")
def business():
    return OntologyRegistry.load()


@pytest.fixture(scope="module")
def catalog():
    return load_catalog_registry()


@pytest.fixture(scope="module")
def harvested():
    # Samples are opted in here to exercise masking; the default harvest takes none.
    return harvest(MemorySource(), list(SYSTEMS), sample_rows=10)


@pytest.fixture(scope="module")
def classified(business, harvested):
    return Classifier(business).classify(harvested)


def field(classification, dataset, name):
    return classification.datasets[dataset].fields[name]


# ------------------------------------------------------------------ registry


def test_catalog_registry_is_independent_and_compatible(business, catalog):
    assert catalog.ontology_version != business.ontology_version
    assert OntologyRegistry.load().ontology_version == business.ontology_version
    for cid in ("SourceSystem", "Dataset", "DataField"):
        base, extended = business.properties_for_class(cid), catalog.properties_for_class(cid)
        assert base.keys() <= extended.keys()
        assert all(base[p].datatype == extended[p].datatype and base[p].required == extended[p].required for p in base)
        assert catalog.get_class(cid).key == business.get_class(cid).key


# ------------------------------------------------------------------ harvest


def test_harvest_profiles_every_source_column_and_excludes_lineage(harvested):
    assert [t.dataset_id for t in harvested.tables][:2] == ["clm.business_case_tech_crosswalk", "clm.canonical_vendor_master"]
    financials = next(t for t in harvested.tables if t.table == "ct_technology_financials")
    assert financials.row_count == 60 and len(financials.columns) == 84
    assert not any(c.name.startswith("_source_") for t in harvested.tables for c in t.columns)
    vendor = financials.column("Vendor_ID")
    assert (vendor.distinct_count, vendor.null_pct, vendor.distinct_values[0]) == (20, 0.0, "V-001")
    assert financials.column("Contract_End_Date").dates and financials.column("Amount").numeric
    assert financials.column("Sep_USD").null_pct == pytest.approx(33.33)
    assert len(financials.sample_rows) == 10
    assert sum(len(t.columns) for t in harvested.tables) == 313 and len(harvested.anchors) == 20


@pytest.mark.parametrize("values,expected", [
    (["V-001", "V-020"], r"^V-\d{3}$"), (["CTR-001"], r"^CTR-\d{3}$"), (["SYN-FIN-2026-001-ACTUAL", "SYN-FIN-2026-001-BUDGET"], None),
    (["Budget", "Actual"], None), (["1.5", "2.5"], r"^\d{1}\.\d{1}$"), (["a(1)"], r"^a\(\d{1}\)$"),
])
def test_value_pattern(values, expected):
    assert value_pattern(values) == expected


def test_default_harvest_sends_the_model_no_sample_rows(business):
    result = harvest(MemorySource(), ["workforce"])
    assert all(t.sample_rows == () for t in result.tables)
    classification = Classifier(business).classify(result)
    model = FakeModel()
    CatalogEnricher(model, business, Classifier(business)).enrich(result, classification)
    assert model.requests and all(payload["sample_rows"] == [] for _, payload, _ in model.requests)


def test_harvest_bounds():
    with pytest.raises(HarvestError):
        harvest(MemorySource(), ["clm"], sample_rows=51)
    with pytest.raises(HarvestError, match="Column limit"):
        harvest(MemorySource(), ["finance"], max_columns=50)
    assert harvest(MemorySource(), ["clm"], sample_rows=0).tables[0].sample_rows == ()


def test_postgres_source_quotes_identifiers_and_binds_values():
    rendered = PostgresCatalogSource.stats_query("workforce", "ct_workforce_organization", ["Vendor_Name", "VENDOR_NAME"]).as_string(None)
    assert '"Vendor_Name"::text' in rendered and '"VENDOR_NAME"::text' in rendered
    assert rendered.endswith('FROM "workforce"."ct_workforce_organization"')
    assert "E'^-?[0-9]+(\\\\.[0-9]+)?$'" in rendered  # constant pattern as an escaped literal


# ---------------------------------------------------------------- classifier


@pytest.mark.parametrize("dataset,column,mapping", [
    ("finance.ct_technology_financials", "Contract_End_Date", "Contract.end_date"),
    ("finance.ct_technology_financials", "Contract_ID", "Contract.contract_id"),
    ("finance.ct_technology_financials", "Vendor_ID", "Vendor.vendor_id"),
    ("finance.ct_technology_financials", "Risk_Tier", "RiskAssessment.risk_tier"),
    ("finance.ct_technology_financials", "Risk_Assessment_Date", "RiskAssessment.assessment_date"),
    ("finance.ct_technology_financials", "Issue_ID", "RiskIssue.issue_id"),  # by identifier values
    ("finance.ct_technology_financials", "SLA_ID", "ServiceLevelAgreement.sla_id"),  # by class synonym
    ("clm.canonical_vendor_master", "Contract_Document_ID", "ContractDocument.document_id"),
    ("workforce.ct_workforce_organization", "Assignment_Start_Date", "Assignment.start_date"),
])
def test_classifier_exact_mappings_are_auto(classified, dataset, column, mapping):
    f = field(classified, dataset, column)
    assert (f.mapping, f.confidence, f.review_state, f.basis) == (mapping, 1.0, "auto", "classifier")
    assert f.description


def test_classifier_roles_value_sets_and_unmapped_columns(classified):
    fin = "finance.ct_technology_financials"
    scenario = field(classified, fin, "Scenario")
    assert (scenario.semantic_role, scenario.value_set, scenario.mapping) == ("dimension", ("Actual", "Budget", "Forecast"), None)
    amount, note = field(classified, fin, "Amount"), field(classified, fin, "Scenario_Note")
    assert (amount.semantic_role, amount.value_set, amount.value_pattern) == ("measure", None, None)
    assert (note.semantic_role, note.value_set) == ("text", None)
    assert field(classified, fin, "Year").semantic_role == "dimension"
    assert field(classified, fin, "Vendor_ID").value_pattern == r"^V-\d{3}$"
    assert field(classified, fin, "Currency").mapping is None  # single-word names are ambiguous across classes


def test_case_collision_flagged_and_not_auto_mapped(classified):
    ds = "workforce.ct_workforce_organization"
    for name in ("Vendor_Name", "VENDOR_NAME"):
        f = field(classified, ds, name)
        assert f.name_collision and f.confidence < 1.0 and f.review_state == "unreviewed"
    assert not field(classified, "finance.ct_technology_financials", "Vendor_Name").name_collision
    assert field(classified, "finance.ct_technology_financials", "Vendor_Name").review_state == "auto"


def test_person_level_fields_are_masked(classified):
    ds = "workforce.ct_workforce_organization"
    for name in ("EMPLID", "NAME"):
        f = field(classified, ds, name)
        assert f.masked and f.value_set is None and f.value_pattern is None


def test_mapping_requires_values_to_fit_the_property(business, harvested):
    table = next(t for t in harvested.tables if t.table == "ct_technology_financials")
    column = replace(table.column("Risk_Tier"), distinct_values=("High", "Severe"))
    classifier = Classifier(business)
    assert classifier.field(column, False).mapping is None
    assert not classifier.compatible("Contract", "end_date", "measure", table.column("Amount"))


def test_domains_and_grain(classified):
    fin = classified.datasets["finance.ct_technology_financials"]
    assert fin.domains["spend_forecast"] == "source_system"
    assert fin.domains["risk_sla_performance"] == "fields"
    assert fin.grain == "One row per record_id within one graph namespace."
    assert "no unique business key" in classified.datasets["dependency.contract_application_bridge"].grain
    workforce = classified.datasets["workforce.ct_workforce_organization"]
    assert "vendor_ownership" not in workforce.domains  # only reviewed-free (auto) mappings assign domains


@pytest.fixture(scope="module")
def references(business, harvested, classified):
    return {r.source: r for r in discover_references(harvested, classified, business)}


@pytest.mark.parametrize("child,home", [
    ("finance.ct_technology_financials.Contract_ID", "clm.canonical_vendor_master.Contract_ID"),
    ("finance.ct_vendor_technology_forecast.Contract_Number", "clm.canonical_vendor_master.Contract_ID"),  # different name
    ("workforce.ct_workforce_organization.Contract_ID", "clm.canonical_vendor_master.Contract_ID"),
    ("dependency.contract_application_bridge.Contract_ID", "clm.canonical_vendor_master.Contract_ID"),
    ("workforce.ct_workforce_organization.Vendor_ID", "clm.canonical_vendor_master.Vendor_ID"),
    ("workforce.ct_workforce_organization.VENDOR_PURCHASE_ORDER", "finance.ct_vendor_technology_forecast.Invoice_PO"),
    ("finance.ct_technology_financials.Organization_ID", "mdm.organization_ou_crosswalk.Organization_ID"),
    ("finance.ct_technology_financials.SLA_ID", "clm.canonical_vendor_master.SLA_ID"),  # reference table, not a fact table
])
def test_foreign_keys_found_from_values(references, child, home):
    ref = references[child]
    assert (ref.target, ref.containment_pct, ref.basis, ref.confidence, ref.review_state) == (home, 100.0, "concept", 1.0, "auto")


def test_pattern_only_references_are_unreviewed(references):
    ref = references["finance.ct_technology_financials.BCID"]
    assert (ref.target, ref.basis, ref.review_state) == ("mdm.vendor_external_id_crosswalk.BCID", "pattern", "unreviewed")
    assert ref.confidence < 1.0


def test_low_information_fields_never_become_keys(references):
    for name in ("Graph_Namespace", "Year", "Scenario", "Currency", "Amount", "Contract_Start_Date", "EMPLID", "NAME"):
        assert not any(k.endswith("." + name) or r.target.endswith("." + name) for k, r in references.items()), name
    assert all(r.containment_pct == 100.0 for r in references.values())


def test_business_names(business, classified):
    assert concept_name(business, "Contract", "end_date") == "Contract end date"
    assert concept_name(business, "RiskAssessment", "risk_tier") == "Risk assessment tier"
    assert concept_name(business, "StatementOfWork", "sow_id") == "Statement of work ID"
    assert field(classified, "finance.ct_technology_financials", "Contract_End_Date").business_name == "Contract end date"
    assert humanize("Col_2026_YTP") == "Col 2026 ytp"


# ------------------------------------------------------------------ enricher


def test_enricher_applies_unreviewed_proposals_without_touching_classifier(business, harvested, classified):
    classifier = Classifier(business)
    model = FakeModel({"Billable": "Assignment.billable", "Amount": "Contract.end_date", "Contract_End_Date": "Vendor.vendor_id"})
    result, failures = CatalogEnricher(model, business, classifier).enrich(harvested, classified)
    assert failures == []
    ds = "workforce.ct_workforce_organization"
    billable = field(result, ds, "Billable")
    assert billable.business_name == "Name of Billable"
    assert (billable.mapping, billable.confidence, billable.review_state, billable.basis) == ("Assignment.billable", 0.9, "unreviewed", "llm")
    assert field(result, "finance.ct_technology_financials", "Amount").mapping is None  # incompatible proposal dropped
    end = field(result, "finance.ct_technology_financials", "Contract_End_Date")
    assert (end.mapping, end.review_state) == ("Contract.end_date", "auto")  # classifier mapping kept
    assert result.datasets[ds].summary and result.datasets[ds].review_state == "unreviewed"
    assert result.datasets[ds].domains["workforce"] == "source_system"
    assert field(classified, ds, "Billable").mapping is None  # input classification unchanged
    assert all(n in [c.name for c in harvested.tables[0].columns] for n in model.requests[0][1]["describe_columns"])


def test_enricher_masks_samples_and_bounds_confidence(business, harvested, classified):
    model = FakeModel({"Billable": "Assignment.billable"}, confidence=1.0)
    result, _ = CatalogEnricher(model, business, Classifier(business)).enrich(harvested, classified)
    assert field(result, "workforce.ct_workforce_organization", "Billable").confidence == 0.95
    sent = json.dumps([p for _, p, _ in model.requests])
    assert "Synthetic resource 001-001" not in sent and "SYN-EMP-001-001" not in sent and "<masked>" in sent
    schema = model.requests[0][2]
    mapping_enum = schema["properties"]["columns"]["items"]["properties"]["ontology_mapping"]["enum"]
    assert "none" in mapping_enum and "Contract.end_date" in mapping_enum and all(m == "none" or "." in m for m in mapping_enum)
    assert not any(m.split(".")[0] in ("Dataset", "DataField", "SourceSystem", "MetricDefinition") for m in mapping_enum)
    forecast = next(p for _, p, _ in model.requests if p["dataset"] == "finance.ct_vendor_technology_forecast")
    ytp = next(c for c in forecast["columns"] if c["name"] == "Col_2026_YTP")
    assert "comment" in ytp  # column comments reach the model


def test_enricher_drops_low_confidence_proposals(business, harvested, classified):
    model = FakeModel({"Billable": "Assignment.billable"}, confidence=0.3)
    result, _ = CatalogEnricher(model, business, Classifier(business)).enrich(harvested, classified)
    billable = field(result, "workforce.ct_workforce_organization", "Billable")
    assert billable.mapping is None and billable.business_name == "Name of Billable"


def test_enricher_failure_keeps_deterministic_classification(business, harvested, classified):
    model = FakeModel(fail={"finance.ct_technology_financials"})
    result, failures = CatalogEnricher(model, business, Classifier(business)).enrich(harvested, classified)
    assert failures == ["finance.ct_technology_financials (batch 1 of 2)", "finance.ct_technology_financials (batch 2 of 2)"]
    assert result.datasets["finance.ct_technology_financials"] == classified.datasets["finance.ct_technology_financials"]


def test_enricher_keeps_first_of_duplicate_columns_and_isolates_failed_batches(business, harvested, classified):
    class Repeating(FakeModel):
        def complete(self, stage, prompt, payload, schema):
            output = super().complete(stage, prompt, payload, schema)
            if output["columns"]:
                output["columns"].append({**output["columns"][0], "description": "Second proposal."})
            if payload["dataset"] == "finance.ct_vendor_technology_forecast" and "summary" not in schema["properties"]:
                raise ModelError("Azure OpenAI request failed: timeout")
            return output
    result, failures = CatalogEnricher(Repeating(), business, Classifier(business), columns_per_call=40).enrich(harvested, classified)
    assert failures == ["finance.ct_vendor_technology_forecast (batch 2 of 3)", "finance.ct_vendor_technology_forecast (batch 3 of 3)"]
    forecast = result.datasets["finance.ct_vendor_technology_forecast"]
    assert forecast.summary and forecast.review_state == "unreviewed"  # batch 1 still applied
    assert not any(f.description == "Second proposal." for d in result.datasets.values() for f in d.fields.values())


def test_enricher_rejects_invented_enum_values(business, harvested, classified):
    class Inventing(FakeModel):
        def complete(self, stage, prompt, payload, schema):
            output = super().complete(stage, prompt, payload, schema)
            if output["columns"]:
                output["columns"][0]["ontology_mapping"] = "Vendor.favourite_colour"
            return output
    _, failures = CatalogEnricher(Inventing(), business, Classifier(business)).enrich(harvested, classified)
    assert {f.split(' (')[0] for f in failures} == {t.dataset_id for t in harvested.tables}


# ------------------------------------------------------------------- builder


@pytest.fixture(scope="module")
def payload(harvested, classified, catalog, business):
    return build_payload(harvested, classified, catalog, business)


def test_payload_maps_and_meets_gate_counts(payload, catalog):
    mapped = GraphMapper(catalog).map(payload)
    assert topology_report(catalog, list(mapped.nodes), list(mapped.edges)).valid
    summary = summarize(payload)
    assert summary["namespace"] == CATALOG_NAMESPACE
    assert {k: summary["entities"][k] for k in ("SourceSystem", "Dataset", "DataField", "BusinessDomain")} == \
        {"SourceSystem": 5, "Dataset": 8, "DataField": 313, "BusinessDomain": 6}
    assert summary["entities"]["Concept"] == len({(e.properties["ontology_class"], e.properties["ontology_property"])
                                                   for e in payload.entities if e.class_id == "DataField" and "ontology_class" in e.properties})
    assert all("business_name" in e.properties for e in payload.entities if e.class_id == "DataField")
    links = {(r.source.identity, r.target.identity): r.properties["keys"] for r in payload.relationships if r.type == "JOINABLE_WITH"}
    assert "Contract_Number -> Contract_ID" in links[("finance.ct_vendor_technology_forecast", "clm.canonical_vendor_master")]
    assert summary["references"]["auto"] > 0 and summary["references"]["unreviewed"] > 0
    assert summary["linked_contracts"] == 20
    assert summary["name_collisions"] == ["workforce.ct_workforce_organization.VENDOR_NAME",
                                          "workforce.ct_workforce_organization.Vendor_Name"]
    systems = sorted(e.properties["source_system_id"] for e in payload.entities if e.class_id == "SourceSystem")
    assert systems == ["PG-CLM", "PG-DEPENDENCY", "PG-FINANCE", "PG-MDM", "PG-WORKFORCE"]


def test_payload_is_deterministic(harvested, classified, catalog, business, payload):
    assert build_payload(harvested, classified, catalog, business) == payload
    assert all(e.provenance[0].extraction_run.startswith("harvest-") for e in payload.entities)


def test_anchor_links_only_for_present_ids(business, catalog):
    source = MemorySource(drop=lambda table, row: table == "workforce" and row["Vendor_ID"] == "V-020")
    result = harvest(source, list(SYSTEMS))
    built = build_payload(result, Classifier(business).classify(result), catalog, business)
    linked = {(r.source.identity, r.target.identity) for r in built.relationships if r.type == "HAS_RECORDS_FOR"}
    assert ("workforce.ct_workforce_organization", "CTR-019") in linked
    assert ("workforce.ct_workforce_organization", "CTR-020") not in linked
    assert ("finance.ct_technology_financials", "CTR-020") in linked
    assert not any(t == "mdm.organization_ou_crosswalk" for t, _ in linked)
    via = {r.properties["via_field"] for r in built.relationships if r.type == "HAS_RECORDS_FOR"}
    assert via <= {"Vendor_ID", "Contract_ID"}


def test_no_row_values_beyond_identifiers_and_value_sets(harvested, payload):
    text = json.dumps(asdict(payload), ensure_ascii=False)
    workforce = next(t for t in harvested.tables if t.table == "ct_workforce_organization")
    financials = next(t for t in harvested.tables if t.table == "ct_technology_financials")
    for table, names in ((workforce, ["NAME", "EMPLID", "JOB_TITLE", "Monthly_Allocated_Service_Fee_USD"]),
                         (financials, ["Amount", "Scenario_Note", "YTD_Amount", "BCID_Name"])):
        for name in names:
            for value in table.column(name).distinct_values:
                if len(value) > 4:
                    assert f'"{value}"' not in text, (name, value)


# --------------------------------------------------------------------- query


class FakeClient:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def read(self, callback, *args):
        client = self

        class Tx:
            def run(self, query, **params):
                client.calls.append((query, params))
                return client.rows
        return callback(Tx(), *args)


def test_find_sources_parameters_and_grouping():
    client = FakeClient([{"concept": "Contract.end_date", "dataset_id": "finance.ct_technology_financials", "fields": []}])
    result = CatalogQueryService(client).find_sources(["Contract.end_date", "RiskAssessment", "Contract.end_date"], domain="spend_forecast")
    query, params = client.calls[0]
    assert query.startswith("// citi-catalog:find-sources") and "$concepts" in query
    assert params["namespace"] == CATALOG_NAMESPACE and params["domain"] == "spend_forecast"
    assert params["concepts"][1] == {"name": "RiskAssessment", "class": "RiskAssessment", "property": None}
    assert result["missing"] == ["RiskAssessment"]
    assert result["sources"]["Contract.end_date"][0]["dataset_id"] == "finance.ct_technology_financials"


def test_join_plan_resolves_key_concept_and_carriers():
    class Plan(FakeClient):
        def read(self, callback, *args):
            client = self

            class Tx:
                def run(self, query, **params):
                    client.calls.append((query, params))
                    if "key-concept" in query:
                        return [{"concept_id": "Contract.contract_id"}]
                    if "join-plan" in query:
                        return [{"home_dataset": "clm.canonical_vendor_master", "key_field": "Contract_ID", "joins": []}]
                    return [{"concept": "Contract.end_date", "dataset_id": "clm.canonical_vendor_master", "fields": []}]
            return callback(Tx(), *args)
    client = Plan([])
    plan = CatalogQueryService(client).join_plan("Contract.end_date")
    assert plan["key_concept"] == "Contract.contract_id" and plan["homes"][0]["key_field"] == "Contract_ID"
    assert plan["carriers"][0]["dataset_id"] == "clm.canonical_vendor_master"
    assert client.calls[0][1]["prefix"] == "Contract." and client.calls[1][1]["concept"] == "Contract.contract_id"


def test_reset_only_deletes_confirmed_catalog_namespaces():
    with pytest.raises(GraphInputError, match="--yes"):
        catalog_cli.reset_namespace(FakeClient([]), CATALOG_NAMESPACE, confirmed=False)
    with pytest.raises(GraphInputError, match="catalog-"):
        catalog_cli.reset_namespace(FakeClient([]), "synthetic-pack-20260928", confirmed=True)


@pytest.mark.parametrize("call", [
    lambda q: q.find_sources(["Contract.end_date} RETURN 1 //"]), lambda q: q.find_sources([]),
    lambda q: q.find_sources(["Contract"], domain="invented"), lambda q: q.describe_dataset("clm.x; DROP"),
    lambda q: q.datasets_for_contract("V-001"), lambda q: q.join_plan("Contract.end_date) DETACH DELETE n //"),
])
def test_query_rejects_unsafe_or_unknown_input(call):
    client = FakeClient([])
    with pytest.raises(GraphInputError):
        call(CatalogQueryService(client))
    assert client.calls == []


# ----------------------------------------------------------------------- cli


def test_cli_dry_run_offline(monkeypatch, capsys, harvested, tmp_path):
    monkeypatch.setattr(catalog_cli, "_harvest", lambda args: harvested)
    output = tmp_path / "payload.json"
    assert catalog_cli.main(["harvest", "--dry-run", "--no-llm", "--output", str(output)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["valid"] and report["database_checked"] is False and report["enrichment"] == "skipped"
    assert json.loads(output.read_text(encoding="utf-8"))["namespace"] == CATALOG_NAMESPACE
