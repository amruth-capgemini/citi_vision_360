// PREPARED ONLY: these read-only statements have not been executed against Neo4j.
// Execute statements separately after explicitly authorized isolated ingestion.
// Required parameter for every statement: namespace = "kg-hardening-phase1".
// Counts below apply to the baseline representative.json, before mutation tests.

// Q1. Vendor -> contract -> concrete clause. Expected: 7 rows (V-001: 4, V-002: 3).
MATCH p = (v:Vendor)-[:PARTY_TO]->(c:Contract)-[:HAS_CLAUSE]->(cl:Clause)
WHERE all(n IN nodes(p) WHERE n._kg_namespace = $namespace)
  AND all(r IN relationships(p) WHERE r._kg_namespace = $namespace)
RETURN v.vendor_id, c.contract_id, cl._kg_class AS clause_class,
       cl._kg_occurrence_id AS occurrence_id, cl.notice_days
ORDER BY v.vendor_id, occurrence_id;

// Q2. Contract -> clause -> evidence document. Expected: 7 rows, 2 distinct documents.
// Evidence is serialized JSON; decode it in the caller, not through an APOC dependency.
MATCH p = (c:Contract)-[:HAS_CLAUSE]->(cl:Clause)-[:EVIDENCED_BY]->(d:ContractDocument)
WHERE all(n IN nodes(p) WHERE n._kg_namespace = $namespace)
  AND all(r IN relationships(p) WHERE r._kg_namespace = $namespace)
RETURN c.contract_id, cl._kg_occurrence_id AS occurrence_id,
       d.document_id, cl._kg_provenance AS evidence_json
ORDER BY c.contract_id, occurrence_id;

// Q3. Operational application support. Expected: V-001 -> APP-001 and APP-002 only.
MATCH p = (v:Vendor)-[:PROVIDES]->(s:Service)-[r:SUPPORTS]->(a:Application)
WHERE all(n IN nodes(p) WHERE n._kg_namespace = $namespace)
  AND all(e IN relationships(p) WHERE e._kg_namespace = $namespace)
RETURN v.vendor_id, s.service_id, a.application_id, a.criticality,
       r._kg_provenance AS relationship_evidence_json
ORDER BY v.vendor_id, a.application_id;

// Q4. Enabling portal use is not operational support. Expected: V-002 -> APP-001.
MATCH p = (v:Vendor)-[:PROVIDES]->(s:Service)-[:USES_PORTAL]->(a:Application)
WHERE all(n IN nodes(p) WHERE n._kg_namespace = $namespace)
  AND all(r IN relationships(p) WHERE r._kg_namespace = $namespace)
RETURN v.vendor_id, s.service_id, a.application_id;

// Q5. Posted actuals by vendor and contract. Expected: 200000 cents per contract,
// 400000 overall. Equal-valued lines are separate records: never SUM(DISTINCT amount).
MATCH p = (v:Vendor)<-[:ISSUED_BY]-(i:Invoice)-[:HAS_LINE]->(l:InvoiceLine)
MATCH q = (i)-[:BILLED_UNDER]->(c:Contract)
WHERE all(n IN nodes(p) + nodes(q) WHERE n._kg_namespace = $namespace)
  AND all(r IN relationships(p) + relationships(q) WHERE r._kg_namespace = $namespace)
  AND i.status = 'Posted' AND l.period = '2025-12'
WITH DISTINCT v, c, l
RETURN v.vendor_id, c.contract_id, count(l) AS line_count,
       sum(l.actual_usd_cents) AS actual_usd_cents,
       sum(l.forecast_usd_cents) AS forecast_usd_cents,
       sum(l.variance_usd_cents) AS variance_usd_cents
ORDER BY v.vendor_id;

// Q6. Invoice line cost allocation. Expected: 4 rows; each contract's 2 lines to its CC.
MATCH p = (c:Contract)<-[:BILLED_UNDER]-(i:Invoice)
          -[:HAS_LINE]->(l:InvoiceLine)-[:CHARGED_TO]->(cc:CostCenter)
WHERE all(n IN nodes(p) WHERE n._kg_namespace = $namespace)
  AND all(r IN relationships(p) WHERE r._kg_namespace = $namespace)
RETURN c.contract_id, i.invoice_id, l.invoice_line_id, cc.cost_center_id
ORDER BY c.contract_id, l.invoice_line_id;

// Q7. Contract-funded service with SLA breach and source. Expected: CTR-001,
// SVC-001, MET-001-01, 99.0 actual vs 99.5 target, V-001_sla_risk_pack.
MATCH p = (c:Contract)-[:HAS_SOW]->(:StatementOfWork)-[:FUNDS]->(s:Service)
          -[:HAS_SLA]->(sla:ServiceLevelAgreement)<-[:MEASURED_AGAINST]-(m:PerformanceMeasurement)
          -[:EVIDENCED_BY]->(d:SlaRiskPackDocument)
WHERE all(n IN nodes(p) WHERE n._kg_namespace = $namespace)
  AND all(r IN relationships(p) WHERE r._kg_namespace = $namespace)
  AND m.breach = true AND m.period = '2026-08'
RETURN c.contract_id, s.service_id, sla.sla_id, m.metric_record_id,
       m.target, m.actual, d.document_id;

// Q8. Open risk finding affecting a critical application. Expected: V-001,
// RA-001, ISSUE-001, APP-001. V-002's Missing assessment has no inferred risk tier.
MATCH p = (v:Vendor)<-[:ASSESSES]-(ra:RiskAssessment)<-[:RAISED_IN]-(issue:RiskIssue)
          -[:AFFECTS]->(a:Application)
WHERE all(n IN nodes(p) WHERE n._kg_namespace = $namespace)
  AND all(r IN relationships(p) WHERE r._kg_namespace = $namespace)
  AND issue.status = 'Open' AND a.criticality = 'Critical'
RETURN v.vendor_id, ra.assessment_id, issue.issue_id, a.application_id;

// Q9. Forward commitments, deliberately separate from posted actuals.
// Expected: 500000 cents per contract, 1000000 overall, period 2026-09.
MATCH p = (po:PurchaseCommitment)-[:COMMITTED_UNDER]->(c:Contract)
WHERE all(n IN nodes(p) WHERE n._kg_namespace = $namespace)
  AND all(r IN relationships(p) WHERE r._kg_namespace = $namespace)
RETURN c.contract_id, po.purchase_order_id, po.period, po.open_commitment_usd_cents
ORDER BY c.contract_id;

// Q10. Vendor -> service -> supported applications -> business product.
// Expected: one DISTINCT result, V-001 / PROD-01, although two application paths exist.
MATCH p = (v:Vendor)-[:PROVIDES]->(:Service)-[:SUPPORTS]->(:Application)-[:ENABLES]->(product:Product)
WHERE all(n IN nodes(p) WHERE n._kg_namespace = $namespace)
  AND all(r IN relationships(p) WHERE r._kg_namespace = $namespace)
RETURN DISTINCT v.vendor_id, product.product_id;
