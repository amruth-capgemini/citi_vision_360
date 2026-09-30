# Knowledge Graph hardening — Phase 1 fixture

This is structured synthetic graph input, not document extraction. No Neo4j operation
is performed by generating or validating it. All provenance is explicitly synthetic
and unreviewed; confidence 1.0 describes fixture construction, not source verification.

## Files

- `representative.json`: self-contained existing GraphPayload contract, pinned to the
  current ontology version. 47 business nodes, 70 relationships, 25 concrete classes,
  24 relationship types. Internal CitiKGScope metadata is not part of these counts.
- `manifest.json`: independently specified exact counts, nine directed path oracles,
  and financial totals. The builder does not calculate or overwrite this oracle.
- `evidence.json`: synthetic assertion records referenced by document citations.
  These are not source PDFs or extracted quotes. Every cited page is page 1 of the
  corresponding one-page synthetic document record. Reference/finance records instead
  cite `synthetic:reference-register-v1`; no unsupported evidence edges are invented.
- `build_fixture.py`: explicitly regenerates payload and evidence deterministically
  from authored facts, obtaining ontology version and keys from the registry.
  Normal tests read the checked-in files; reproducibility is checked in a temp directory.
- `queries.cypher`: ten parameterized, read-only statements for later authorized live
  testing, with expected baseline results. Prepared only; not executed in Phase 1.

## Scenario

As of 2026-09-29, Example Regional Bank has two suppliers and two contracts:

- V-001 Example Technology delivers SVC-001, operationally supporting APP-001 (Critical)
  and APP-002 (Standard). Its contract contains two distinct RenewalClauses, a
  TerminationClause, and a PaymentTermsClause. A recovery exercise deliverable is
  overdue; a high-severity open finding affects SVC-001 and APP-001.
- V-002 Example Facilities delivers SVC-002 supporting branch facilities maintenance.
  It uses APP-001 as a portal; it does not operationally support it. Its contract has
  manual renewal, termination and payment clauses. Its risk assessment explicitly
  records Missing; no date or risk tier is fabricated.

Each contract has its Vendor and the shared Buyer as parties, one SOW funding its
service, a shared oversight Owner, and document evidence. Each service has a CI,
SLA and August 2026 measurement. Technology breaches its 99.5% target at 99.0%;
facilities attains 99.9%. Unit counts reconcile to these percentages.

December 2025 posted actuals are 400000 USD cents across four distinct equal-valued
invoice lines, versus forecast 360000 and variance 40000. Separate September 2026
purchase commitments total 1000000 cents. Never add commitments to posted spend.
Equal line amounts deliberately catch the incorrect `SUM(DISTINCT amount)` pattern.

The graph is one weakly connected component. Its document classes remain separate
from Contract business entities. Stable clause occurrence IDs are explicit literals;
they are never derived from mutable text. Two renewal provisions under CTR-001 use
`renew1` and `renew-support-extension`.

## Offline checks

From repository root, using the existing environment:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m pytest tests/test_graph_representative.py -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\citi-kg.exe ingest tests/fixtures/knowledge_graph/representative.json --dry-run
```

The tests additionally run the existing topology validator: CLI dry-run alone checks
mapping and is not a complete endpoint/topology check. Service replay/update tests
use the existing MemoryClient transaction double, never a database. They do not prove
server-side Cypher execution or live concurrency semantics.

To regenerate intentionally after reviewing fixture/ontology changes:

```powershell
.\.venv\Scripts\python.exe tests/fixtures/knowledge_graph/build_fixture.py
```

Do not regenerate automatically to hide an ontology-version mismatch. Review changes
to the fixture and independent manifest together. No fixture contains credentials.

## Prepared query expectations

Supply `$namespace = 'kg-hardening-phase1'` as a driver/Query API parameter. Execute
each statement separately only in a later explicitly authorized isolated live test.

| Query | Baseline expectation |
| --- | --- |
| Q1 Vendor/contract/clause | 7 rows, four provisions for V-001 and three for V-002 |
| Q2 Clause/document | 7 rows, two distinct ContractDocuments |
| Q3 Operational support | V-001 supports APP-001 and APP-002 |
| Q4 Portal use | V-002 uses APP-001; this is not SUPPORTS |
| Q5 Posted spend | 200000 cents per contract, 400000 total |
| Q6 Cost allocation | Four line rows, two per cost center |
| Q7 SLA breach | One measurement: MET-001-01 |
| Q8 Risk finding | ISSUE-001 affects critical APP-001 for V-001 |
| Q9 Commitments | 500000 cents per contract, excluded from Q5 |
| Q10 Business product | One distinct vendor/product pair: V-001 / PROD-01 |

## Scope and ontology limits

- `SUPPORTS` and `USES_PORTAL` are disjoint for the same directed pair.
- `ASSESSES` is many-to-one: each assessment targets only one subject here.
- `EVIDENCED_BY` does not admit Vendor, Invoice, RiskIssue or Application as subjects.
  Those records can still carry the existing provenance envelope.
- Document identifiers follow the ontology's vendor-specific catalog patterns; no
  extra document version nodes or invented relationship properties are introduced.
- Additional evidence records do not create parallel logical edges. Graph identity
  is based on type and endpoints; citations belong to that single edge.
- Revision tests are controlled structural mutations, not newly evidenced business
  assertions. The baseline fixture and retained evidence are never modified by tests.
- This phase does not implement retirement, deletion, volume testing or schema changes.

The fixture is ready for planning an isolated live ingestion test. Live compatibility,
transport behavior and these Cypher results remain unverified until that separate run.
