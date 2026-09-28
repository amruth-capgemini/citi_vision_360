---
title: Method 1 Assumptions Log
description: FSD evidence, assumptions, and validation questions for scope conversations
---

## Known from the FSD

* The capability is intended to support vendor, sourcing, renewal, risk, rationalization, forecast, and scenario decisions
* The initial demo excludes source-system execution and autonomous approval
* Answers must expose source evidence, as-of dates, assumptions, confidence, policy checks, and limitations
* Eight primary use cases are specified: Vendor 360, renewal prioritization, concentration risk, rationalization, spend variance, scenario analysis, executive Q&A, and alerts

## Assumptions Requiring Validation

| ID | Assumption | Risk if false | Conversation prompt |
| --- | --- | --- | --- |
| A-01 | A stable vendor identifier can connect the relevant source domains | Vendor 360 and cross-domain analysis require manual reconciliation | Where do vendor identities fail to match today, and who resolves them? |
| A-02 | Historical snapshots exist or can be created | Trend, variance, and point-in-time explanations are limited | Which time comparisons are essential for a decision? |
| A-03 | Application or product ownership and criticality are accessible | Dependency and concentration analysis is incomplete | Which criticality and ownership data is reliable enough to use? |
| A-04 | Contract metadata provides dates, obligations, values, and vendor mappings | Renewal prioritization needs extraction or enrichment | Which contract fields are trusted, and where are they missing? |
| A-05 | Required access controls can be enforced | Sensitive evidence may need restricted patterns or separate indexes | Which evidence cannot be surfaced together, even to authorized users? |
| A-06 | Citi will define thresholds and approved scoring logic | Scores remain illustrative and cannot guide a real decision | Who owns thresholds, and how are disagreements resolved? |

## Additional Design-Thinking Assumptions

* The FSD describes user needs accurately enough to select interview participants
* Evidence-assembly effort is a more important pain point than a missing dashboard alone
* A single interaction pattern can serve executive, operational, financial, technology, and risk decisions without concealing material differences

## Evidence Needed Next

Interview examples of a recent renewal, dependency-risk, or spend-variance decision, including the sources consulted, time spent, people involved, points of disagreement, and the proof needed to act.
