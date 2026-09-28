# SLA, commercial performance and risk schedule

SYNTHETIC DEMO DATA - NOT AN EXECUTED AGREEMENT

Vendor: V-011 | As of 2026-09-28 | Buyer: Meridian Vale Financial Group

## Service and commercial commitments

This fictional schedule applies to Backup and recovery operations, supplied by Caldris Recovery Services, for service SVC-011 under CTR-011. All thresholds and credit rates are invented for the demo and are not taken from a real supplier agreement.

Measure | Target / method | Evidence and owner
--- | --- | ---
Primary service measure | 98.0% successful scheduled backup jobs per calendar month | Time or service-order record; Resilience owner
Priority 1 acknowledgement | 15 minutes | Incident log; supplier service lead
Priority 1 restoration objective | 60 minutes, subject to evidenced scope/dependencies | Recovery runbook and owner validation
Invoice accuracy | 99.0% of invoiced lines match approved price and scope | Finance reconciliation; commercial owner
Monthly reporting | By fifth business day of following month | Accepted service report

Primary metric calculation: 100 x (eligible units minus failed units) / eligible units. Units are backup jobs. Only pre-approved, recorded exclusions can change the denominator; supplied observations have zero exclusions.

Incident priority is assigned using business impact. The customer and supplier agree the affected service before applying a remedy. Acknowledgement and restoration objectives are separate from the monthly primary metric.

Measurement scope includes only this contracted service. Upstream failures still require coordination and evidence; they are not silently deleted from records.

## Measured performance and credit illustration

Month | Eligible / failed | Actual % | Target % | Est. credit USD
--- | --- | --- | --- | ---
2026-06 | 1200 / 12 | 99.0 | 98.0 | $0.00
2026-07 | 1200 / 12 | 99.0 | 98.0 | $0.00
2026-08 | 1200 / 12 | 99.0 | 98.0 | $0.00

Invented credit policy: when the primary result misses target by less than 1 percentage point, estimate 3% of the affected monthly fee; at a shortfall of at least 1 percentage point, estimate 7%. No primary breach means zero credit. Total monthly remedies are capped at 15% of the affected service fee, and overlapping incidents are not double-counted.

Fee basis for the above illustration: $166,666.66 USD per month. Claims must identify the service, measurement period and source records within 45 days after month end. The customer reviewer validates exclusions, entitlement and fee basis before accepting a claim.

Estimated credits are not approved refunds and have not been posted into the closed FY2025 finance ledger. The JSON measurement rows preserve the numerator, denominator, target, calculation and credit status.

The acknowledgement and invoice-accuracy targets are commitments only; this fixture does not invent measured results for them. Missing observations must be reported as not measured.

## Risk assessment, controls and review

Field | Recorded state
--- | ---
Assessment ID | RA-011
Assessment date | 2026-08-15
Assessment freshness | Current
Risk tier | Low
Data classification | Internal synthetic
Finding | Recovery exercise evidence incomplete
Issue status / severity | Closed / Medium
Due date / owner | 2026-10-28 / OWN-011
Control ID | CTRL-011

Control purpose: maintain evidence of recovery capability or alternate staffing and a named owner. Open and overdue findings constrain transition recommendations. Closed findings retain historical evidence and do not become newly open solely because a reporting period changes.

A risk assessment older than 365 days is stale under this fictional policy. A missing assessment has no assigned tier; the system must not silently substitute a low-risk rating. The original tier of a stale assessment remains historical evidence, not a current approval.

Human review: commercial owner validates claims and clauses; service owner validates continuity and dependencies; risk owner reviews open findings. The solution may recommend options but never renew, offboard or approve a supplier automatically.

