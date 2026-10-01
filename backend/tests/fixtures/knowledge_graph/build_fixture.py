"""Explicitly rebuild the synthetic fixture. Never executed by tests or on import."""
import json
from pathlib import Path

from citi_project.services.ontology import OntologyRegistry


def build(output_dir=None):
    registry = OntologyRegistry.load()
    entities, relationships, evidence = [], [], {}
    refs = {}

    def cite(doc, subject, properties):
        key = f"synthetic:{doc}:{subject}"
        evidence[key] = {"document_id": doc, "page": 1, "section": subject,
                         "chunk_id": subject, "assertion": properties, "synthetic": True}
        return [{"document": {"class_id": docs[doc], "identity": doc}, "page": 1,
                 "section": subject, "chunk_id": subject, "source_ref": "kg-hardening-fixture-v1",
                 "evidence_ref": key, "confidence": 1.0, "review_state": "unreviewed"}]

    docs = {}
    def entity(alias, cls, props, doc=None, **identity):
        row = {"class_id": cls, "properties": props, "revision": 1, **identity}
        definition = registry.get_class(cls)
        refs[alias] = {"class_id": cls, **identity} if definition.kind == "clause" else {
            "class_id": cls, "identity": props[definition.key]}
        if doc:
            row["provenance"] = cite(doc, alias, props)
        else:
            row["provenance"] = [{"source_ref": "synthetic:reference-register-v1", "review_state": "unreviewed"}]
        entities.append(row)

    def edge(kind, source, target, doc=None):
        row = {"type": kind, "source": refs[source], "target": refs[target], "properties": {}, "revision": 1}
        if doc:
            row["provenance"] = cite(doc, f"{source}-{kind}-{target}", {"type": kind, "source": refs[source], "target": refs[target]})
        else:
            row["provenance"] = [{"source_ref": "synthetic:reference-register-v1", "review_state": "unreviewed"}]
        relationships.append(row)

    entity("buyer", "Buyer", {"legal_name": "Example Regional Bank"})
    entity("owner", "Owner", {"owner_id": "OWN-001", "role": "Supplier oversight lead"})
    entity("product", "Product", {"product_id": "PROD-01", "name": "Retail banking"})
    entity("process", "BusinessProcess", {"process_id": "PROC-001", "name": "Branch facilities maintenance"})
    for i in (1, 2):
        code = f"{i:03}"
        for cls, suffix in (("ContractDocument", "contract_sow"), ("DependencyRegisterDocument", "dependency_register"),
                            ("SlaRiskPackDocument", "sla_risk_pack")):
            doc = f"V-{code}_{suffix}"
            docs[doc] = cls
            entity(doc, cls, {"document_id": doc, "pages_expected": 1})
        contract_doc, dependency_doc, risk_doc = (f"V-{code}_{suffix}" for suffix in ("contract_sow", "dependency_register", "sla_risk_pack"))
        entity(f"v{i}", "Vendor", {"vendor_id": f"V-{code}", "legal_name": ["Example Technology", "Example Facilities"][i-1],
                                   "vendor_type": "Technical" if i == 1 else "Non-technical", "status": "Active"})
        entity(f"c{i}", "Contract", {"contract_id": f"CTR-{code}", "start_date": "2025-01-01", "end_date": "2026-12-31",
                                      "pricing_model": "Fixed managed service fee"}, contract_doc)
        entity(f"sow{i}", "StatementOfWork", {"sow_id": f"SOW-{code}"}, contract_doc)
        for alias, cls, props in ((f"renew{i}", "RenewalClause", {"automatic_renewal": i == 1, "notice_days": 30 if i == 1 else 60}),
                                 (f"term{i}", "TerminationClause", {"termination_convenience_days": 30}),
                                 (f"pay{i}", "PaymentTermsClause", {"payment_terms_days": 30, "billing_currency": "USD"})):
            entity(alias, cls, props, contract_doc, contract_identity=f"CTR-{code}", occurrence_id=alias)
            edge("HAS_CLAUSE", f"c{i}", alias, contract_doc)
            edge("EVIDENCED_BY", alias, contract_doc, contract_doc)
        entity(f"svc{i}", "Service", {"service_id": f"SVC-{code}", "service_name": ["Application support", "Facilities maintenance"][i-1]}, dependency_doc)
        entity(f"app{i}", "Application", {"application_id": f"APP-{code}", "name": ["Retail ledger", "Service portal"][i-1],
                                           "criticality": "Critical" if i == 1 else "Standard"}, dependency_doc)
        entity(f"ci{i}", "ConfigurationItem", {"ci_id": f"CI-{code}-1", "ci_type": "Monitoring endpoint"}, dependency_doc)
        entity(f"sla{i}", "ServiceLevelAgreement", {"sla_id": f"SLA-{code}", "metric": "Monthly attainment", "target_percent": 99.5}, risk_doc)
        entity(f"m{i}", "PerformanceMeasurement", {"metric_record_id": f"MET-{code}-01", "period": "2026-08",
               "metric_name": "Monthly attainment", "target": 99.5, "actual": 99.0 if i == 1 else 99.9,
               "eligible_units": 1000, "failed_units": 10 if i == 1 else 1, "breach": i == 1}, risk_doc)
        entity(f"ra{i}", "RiskAssessment", {"assessment_id": f"RA-{code}", "status": "Current" if i == 1 else "Missing",
               **({"assessment_date": "2026-08-01", "risk_tier": "Medium", "validity_days": 365} if i == 1 else {})}, risk_doc)
        entity(f"cc{i}", "CostCenter", {"cost_center_id": f"CC-{i:02}"})
        entity(f"inv{i}", "Invoice", {"invoice_id": f"INV-{code}-202512", "period": "2025-12", "currency": "USD", "status": "Posted"})
        entity(f"po{i}", "PurchaseCommitment", {"purchase_order_id": f"PO-{code}-2026", "period": "2026-09", "currency": "USD", "open_commitment_usd_cents": 500000})
        for j in (1, 2):
            entity(f"line{i}{j}", "InvoiceLine", {"invoice_line_id": f"INV-{code}-202512-{j:02}", "period": "2025-12", "currency": "USD",
                   "actual_usd_cents": 100000, "forecast_usd_cents": 90000, "variance_usd_cents": 10000,
                   "volume_change_usd_cents": 10000, "rate_mix_usd_cents": 0, "scope_change_usd_cents": 0, "forecast_version": "FY2025-approved-v1"})
            edge("HAS_LINE", f"inv{i}", f"line{i}{j}")
            edge("CHARGED_TO", f"line{i}{j}", f"cc{i}")
        for kind, source, target, doc in (
            ("PARTY_TO", f"v{i}", f"c{i}", contract_doc), ("PARTY_TO", "buyer", f"c{i}", contract_doc),
            ("HAS_SOW", f"c{i}", f"sow{i}", contract_doc), ("FUNDS", f"sow{i}", f"svc{i}", contract_doc),
            ("OWNED_BY", f"c{i}", "owner", contract_doc), ("PROVIDES", f"v{i}", f"svc{i}", dependency_doc),
            ("DEPENDS_ON", f"svc{i}", f"ci{i}", dependency_doc), ("ENABLES", f"app{i}", "product", dependency_doc),
            ("HAS_SLA", f"svc{i}", f"sla{i}", risk_doc), ("MEASURED_AGAINST", f"m{i}", f"sla{i}", risk_doc),
            ("ASSESSES", f"ra{i}", f"v{i}", risk_doc), ("ISSUED_BY", f"inv{i}", f"v{i}", None),
            ("BILLED_UNDER", f"inv{i}", f"c{i}", None), ("COMMITTED_UNDER", f"po{i}", f"c{i}", None)):
            edge(kind, source, target, doc)
        for source, doc in ((f"c{i}", contract_doc), (f"svc{i}", dependency_doc), (f"sla{i}", risk_doc),
                            (f"m{i}", risk_doc), (f"ra{i}", risk_doc)):
            edge("EVIDENCED_BY", source, doc, doc)
    entity("renew-extra", "RenewalClause", {"automatic_renewal": True, "notice_days": 90, "section_title": "Optional support extension"},
           "V-001_contract_sow", contract_identity="CTR-001", occurrence_id="renew-support-extension")
    entity("delivery", "Deliverable", {"deliverable_id": "SOW-001-D1", "output": "Recovery exercise",
                                       "acceptance_test": "Demonstrate recovery within the agreed target"}, "V-001_contract_sow")
    entity("issue", "RiskIssue", {"issue_id": "ISSUE-001", "finding": "Recovery exercise overdue", "status": "Open", "severity": "High"}, "V-001_sla_risk_pack")
    for kind, source, target, doc in (
        ("HAS_CLAUSE", "c1", "renew-extra", "V-001_contract_sow"), ("EVIDENCED_BY", "renew-extra", "V-001_contract_sow", "V-001_contract_sow"),
        ("HAS_DELIVERABLE", "sow1", "delivery", "V-001_contract_sow"), ("ACCEPTED_BY", "delivery", "owner", "V-001_contract_sow"),
        ("SUPPORTS", "svc1", "app1", "V-001_dependency_register"), ("SUPPORTS", "svc1", "app2", "V-001_dependency_register"),
        ("USES_PORTAL", "svc2", "app1", "V-002_dependency_register"), ("SUPPORTS_PROCESS", "svc2", "process", "V-002_dependency_register"),
        ("RAISED_IN", "issue", "ra1", "V-001_sla_risk_pack"), ("AFFECTS", "issue", "svc1", "V-001_sla_risk_pack"),
        ("AFFECTS", "issue", "app1", "V-001_sla_risk_pack"), ("OWNED_BY", "issue", "owner", "V-001_sla_risk_pack")):
        edge(kind, source, target, doc)
    payload = {"namespace": "kg-hardening-phase1", "ontology_version": registry.ontology_version,
               "entities": entities, "relationships": relationships}
    root = Path(output_dir) if output_dir is not None else Path(__file__).parent
    for name, value in (("representative.json", payload), ("evidence.json", evidence)):
        (root / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    build()
