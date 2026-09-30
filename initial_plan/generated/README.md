# Correlated synthetic structured data
Namespace: synthetic-pack-20260928. Canonical snapshot: SNAP-20260928, as of 2026-09-28.
This is synthetic planning data, not posted finance actuals, approved forecasts, or verified source evidence.
The source pack, PDFs, ontology and graph are unchanged. Never join this dataset to kg-hardening-phase1 using bare IDs.

## Files and identity
canonical_vendor_master.csv: one row per canonical vendor/contract.
business_case_tech_crosswalk.csv: new synthetic VRM is in master; BC20001..BC20020 and T2001..T2020 map explicitly to contracts/services.
organization_ou_crosswalk.csv: ORG-01..ORG-04 map to OU3101..OU3104; these are separate identity domains.
contract_application_bridge.csv: exact live graph SUPPORTS and USES_PORTAL edges, one row per directed connection.
All original CSVs are preserved byte-for-byte in ../archive_original_csv with a SHA-256 manifest.

## Financial model and periods
USD decimals use two places; source annual_base_fee_usd_cents is divided by 100.
Budget monthly cents = source annual baseline * active contract days in month / days in year, rounded half up.
Actual is a synthetic Jan-Aug 2026 scenario, never a full-year or posted actual. Sep-Dec Actual and FY_USD are blank.
Forecast uses Actual for Jan-Aug, and baseline * explicit remaining-period multiplier for Sep-Dec.
The per-vendor multipliers are retained in the financial CSV. Monthly rounded amounts sum exactly to row totals.
Amount is YTD for Actual and FY for Budget/Forecast; compare YTD_Amount across all three scenarios.
FY_USD is a convenience alias, not an additional metric to sum with Amount.
Col_2026_YTP means year-to-proceed forecast for Sep-Dec. Actual_YTD_2026_USD + Col_2026_YTP = Forecast_2026_USD.
2027 uses the same annual baseline, prorated to recorded expiry. 2028/2029 are zero because no source contract extends into them.
No extension, negotiated rate increase, approved budget or completed risk assessment is asserted.
Lifecycle Contract_Type is Renewal when expiry is within 120 days, otherwise Committed; it describes planning classification, not an executed renewal.
Contract_Format preserves the source engagement format.
V-001 retains its August breach and remediation caveat; it is not labeled uniformly good SLA performance.
RA-009 remains Stale. RA-017 remains Missing with blank assessment date and risk tier.

## Workforce
Existing Assignment_IDs, worker aliases, roles, locations, billable flags, FTE and allocated fees are reused.
Select four assignments for populations >=20, three for >=8, otherwise two; include one existing shadow where present.
This is a representative subset of 283 assignments, not full headcount and not statistical expansion weights.
Employee/Contractor/Consultant describes hypothetical vendor-side employment; none asserts buyer employee status.
EMPLID, employment type, managerial/delivery classifications and RTB/CTB splits are synthetic enrichment.
Onshore means US relative to the synthetic US buyer; other source locations are offshore.
Source assignment Application_ID stays blank where absent, even when its service uses a portal.
Allocated service fees are existing attribution data, not additional vendor expense; never add them to financial spend.
PO links identify the existing contract-level 2026 commitment, not a source assertion of assignment-level PO allocation.

## Join rules
Use Graph_Namespace + Vendor_ID + Contract_ID to join each fact to the master.
Service_ID, SOW_ID, financial Product_ID and Organization_ID must match the canonical source allocation.
Application product may differ from financial Product_ID; do not overwrite one with the other.
Join crosswalk on namespace/contract/service and effective dates.
For finance, key additionally includes Year and Scenario. Do not sum scenarios together.
Forecast and financial Forecast describe the same modeled spend; do not union and sum them.
Aggregate workforce by namespace/contract/service before combining it with financial totals.
Do not join raw financial amounts to multiple applications or assignments and then sum them.
Use the application bridge for dependency filtering (existence/semi-join) or distinct entity counts; allocations need explicit weights.
Neo4j: namespace-bound Vendor PARTY_TO Contract HAS_SOW SOW FUNDS Service; Service SUPPORTS/USES_PORTAL Application.
No schema changes, graph writes, ingestion, agents or semantic layer were performed.

## Schema and validation
schema_lineage.json exhaustively lists retained, dropped, added and renamed columns and their origin.
Original CSV values were not reused; only selected useful column names were retained.
Dropped fields include unsupported HR hierarchy/address/contact fields, legacy ambiguous IDs/flex columns,
forecast months after 2029, and financial effort measures without a source basis.
DAF_Number, Bundle_ID and DAF_Amount are blank: the canonical pack does not establish approvals or bundles.
validation_report.json records all 13 rules, output hashes, exact row counts, totals, samples and identical graph fingerprints.

The vendor_external_id_crosswalk.csv explicitly records all 20 VRM/BCID/Tech_ID assignments and the OU crosswalk alongside canonical IDs. These external IDs are synthetic-only and are not Neo4j entities.
