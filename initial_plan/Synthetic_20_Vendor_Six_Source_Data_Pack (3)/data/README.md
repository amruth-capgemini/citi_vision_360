# Synthetic vendor decision-intelligence data

SYNTHETIC DEMO DATA - NOT AN EXECUTED AGREEMENT

20 fictional vendors: 12 technical and 8 non-technical. Buyer: Meridian Vale Financial Group. Snapshot: 2026-09-28.

## Six source classes
1. `01_vendor_ownership`: Legal entities, aliases, source IDs, parents, products, organizations and owners.
2. `02_contracts_sows`: 20 structured contracts and 20 PDF SOWs with scope, resources, charges, notices and exit terms.
3. `03_spend_forecast`: 240 monthly actual/forecast records, reconciling variance drivers and 20 forward commitments.
4. `04_workforce`: Assignment IDs, roles, location, contract/service references and allocated fees.
5. `05_service_dependencies`: Technical assets, business processes, enabling portals, recovery objectives and 20 dependency PDFs.
6. `06_risk_sla_performance`: 20 SLA/risk PDFs, 60 measured results, risks, issues, credit estimates and explicit gaps.

There are 20 contract/SOW PDFs (4 pages each), 20 dependency PDFs (2 pages each) and 20 SLA/risk PDFs (3 pages each): 60 PDFs / 180 pages. Each PDF also has a Markdown rendition for retrieval. Every vendor has a JSON source file in each of the six classes, for 120 vendor JSON files.

## Engagement diversity
Twenty tailored scopes use ten contract families: capacity support, managed service, fixed-price delivery, capped T&M, agile delivery, research stage-gates, SaaS subscriptions, unit-rate services, training call-offs and advisory milestones. V-004 is ETL, V-008 data science, and V-012 BI dashboards. See `engagement_format_matrix.json` for exact vendor mapping.

## Start here
Open `INDEX.html` locally after extracting the ZIP for a clickable vendor and document catalog.
Open `INDEX.html` locally after extracting the ZIP for a clickable vendor and document catalog.
Open `vendor_catalog.json` to find the six files for a vendor. Contract, service, assignment and evidence IDs join across classes. Read `source_classes_and_agent_mapping.json` for SQL, semantic, graph, document and agent destinations. The PDF files simulate original source documents; matching JSON files simulate structured operational exports.

Load only one of PDF or Markdown for each document ID. Do not index golden answers as business evidence. The catalogs and rules are support metadata, not extra spend or workforce rows. Amounts are integer USD cents. Finance is the closed 2025 calendar year; SLA observations are June-August 2026; current state is 2026-09-28. The 2026 PO commitments and proposed service credits must not be added to 2025 actuals.

## Demonstration cases
- V-001: critical support, expiry in 90 days, 2025 overspend and an August SLA breach.
- V-001 and V-009: related suppliers with overlap; alternative capacity unvalidated and V-009 risk assessment stale.
- V-005: near expiry and overdue notice/control issue.
- V-013: non-technical facilities dependency; consuming an application is not operating it.
- V-017: missing assessment, with no invented risk tier.
- The scenario example is hypothetical. Spend reduction does not imply automatic removal of people or applications.

`golden_questions_and_expected_answers.json` provides evidence pointers and test expectations. `demo_semantic_definitions.json` records the invented metric policies. `data_dictionary.json` documents keys, units and source grain. `validation_report.json` records consistency checks.

## Provenance and exclusions
The supplied deck determined the domain grouping. The uploaded redacted SOW informed document organization only; it is not distributed in this package. All prices, thresholds, names, roles, contacts, dates, findings and approvals are artificial. Redacted amounts were not recovered. Source branding, names, addresses, signatures and project identifiers were not reused. The actual reference contains an absent SLA schedule and inconsistent resource counts, so these were replaced by internally reconciled fictional terms.

Web research sources and the limited concepts used are recorded in `research_and_provenance.json`. These are invented demo documents, not legal templates, live integrations, approved policies or real supplier assessments.

## Reset scope
No pre-existing folder named `data` was found in the accessible workspace or file library. This is a freshly created folder; unrelated source files and presentations were preserved.
