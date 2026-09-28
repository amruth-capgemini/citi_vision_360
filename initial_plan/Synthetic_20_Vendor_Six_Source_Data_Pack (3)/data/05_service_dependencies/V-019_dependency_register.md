# Service and dependency register

SYNTHETIC DEMO DATA - NOT AN EXECUTED AGREEMENT

Vendor: V-019 | As of 2026-09-28 | Buyer: Meridian Vale Financial Group

## Dependency register

Quenora Logistics (V-019) delivers Internal courier and distribution under CTR-019 and SOW-019. The accountable owner is Logistics owner (OWN-019). This document describes source relationships for later graph projection, not an assertion of production discovery.

Object ID | Object or application | Criticality / role | Relationship
--- | --- | --- | ---
PROC-019 | Internal courier and distribution | Business process | SUPPORTS_PROCESS
SITE-02 | Workplace location | Facility | DELIVERED_AT
APP-020 | Logistics Portal | Enabling portal | USES_PORTAL

Trace: V-019 PROVIDES SVC-019; CTR-019 HAS_SOW SOW-019; SOW-019 FUNDS SVC-019. The vendor uses an enabling portal but does not support or operate its software. No application-support relationship is invented.

Every relationship carries a source ID, evidence reference, verification date and effective interval. Technical configuration items are listed in the matching JSON export. Organization/product accountability comes from the reference entities file.

Upstream prerequisites: authorized access, approved source inventories and customer change windows. Downstream effects require the service and asset owners to validate impact; a dependency alone does not establish an outage.

## Continuity, substitution and impact

Recovery time objective for the supplier service: 240 minutes. Recovery point objective: not applicable to this non-technical service; use process/custody reconstruction procedures.

Recovery actions: identify the affected scope, invoke the named owner, use the approved fallback, confirm minimum service and reconcile outstanding work before restoring normal operations. Test evidence is managed through the risk/control record.

Potential overlap candidate: none preselected. Capacity and transition readiness: Not assessed. Capability overlap does not constitute an approved replacement.

Commercial dependencies: notice deadline 2027-01-06, early-exit fee 0 monthly base fees, and a separately priced transition scope. Compare these with remaining contract term before estimating savings.

Impact query rules: count distinct affected applications, processes and assignments. A reduction in spend does not imply an equal percentage reduction in resources. Shared applications may depend on several vendors and must not be double-counted.

Evidence status: synthetic mappings are verified as fixtures on 2026-09-20. The intentional unvalidated alternative capacity must remain visible in any generated recommendation.

