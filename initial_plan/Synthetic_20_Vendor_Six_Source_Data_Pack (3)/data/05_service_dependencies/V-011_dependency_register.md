# Service and dependency register

SYNTHETIC DEMO DATA - NOT AN EXECUTED AGREEMENT

Vendor: V-011 | As of 2026-09-28 | Buyer: Meridian Vale Financial Group

## Dependency register

Caldris Recovery Services (V-011) delivers Backup and recovery operations under CTR-011 and SOW-011. The accountable owner is Resilience owner (OWN-011). This document describes source relationships for later graph projection, not an assertion of production discovery.

Object ID | Object or application | Criticality / role | Relationship
--- | --- | --- | ---
APP-001 | Payment Routing | Critical | SUPPORTS
APP-002 | Card Authorization | Critical | SUPPORTS
APP-004 | Settlement Control | Critical | SUPPORTS
APP-005 | Cloud Control | Critical | SUPPORTS

Trace: V-011 PROVIDES SVC-011; CTR-011 HAS_SOW SOW-011; SOW-011 FUNDS SVC-011. 6 workforce assignments support the service. 4 distinct critical applications are supported.

Every relationship carries a source ID, evidence reference, verification date and effective interval. Technical configuration items are listed in the matching JSON export. Organization/product accountability comes from the reference entities file.

Upstream prerequisites: authorized access, approved source inventories and customer change windows. Downstream effects require the service and asset owners to validate impact; a dependency alone does not establish an outage.

## Continuity, substitution and impact

Recovery time objective for the supplier service: 60 minutes. Recovery point objective: 15 minutes for recoverable data.

Recovery actions: identify the affected scope, invoke the named owner, use the approved fallback, confirm minimum service and reconcile outstanding work before restoring normal operations. Test evidence is managed through the risk/control record.

Potential overlap candidate: none preselected. Capacity and transition readiness: Not assessed. Capability overlap does not constitute an approved replacement.

Commercial dependencies: notice deadline 2027-01-26, early-exit fee 0 monthly base fees, and a separately priced transition scope. Compare these with remaining contract term before estimating savings.

Impact query rules: count distinct affected applications, processes and assignments. A reduction in spend does not imply an equal percentage reduction in resources. Shared applications may depend on several vendors and must not be double-counted.

Evidence status: synthetic mappings are verified as fixtures on 2026-09-20. The intentional unvalidated alternative capacity must remain visible in any generated recommendation.

