---
title: Citi Vendor Decision Intelligence Preliminary Design
description: FSD-informed target design with explicit Design Thinking validation gates
ms.date: 2026-09-27
---

## Status and Evidence

This document translates the supplied Citi Vendor Decision Intelligence FSD into a target design. It is not a claim that all nine Design Thinking methods have been completed. The FSD and Method 1 scope artifacts are the current evidence; user research, prototype feedback, technical validation, and production telemetry still need to be gathered.

## Design Intent

Enable authorized Citi stakeholders to move from a vendor decision question to an evidence-backed, explainable, non-destructive recommendation without manually reconciling fragmented commercial, financial, workforce, technology, risk, and ownership information.

The product is a governed decision-support workbench, not an autonomous approval or system-of-record replacement.

## Problem Framing

The fragmented identity, freshness, access, and relationship context of vendor information is producing slow and difficult-to-defend decisions for OCIO executives, vendor management, sourcing, technology, finance, and third-party risk teams. How might Citi connect entitled decision evidence so those teams can assess vendor actions with traceable confidence?

## Experience Principles

* Show the answer, the evidence, and the limitation together
* Enforce entitlement checks before retrieval, reasoning, display, and export
* Treat recommendations as options requiring human approval
* Preserve effective date, as-of date, source, and calculation context in every decision view
* Make missing data, unresolved identity, policy conflicts, and confidence visible rather than smoothing them away
* Keep scenarios non-destructive and separate from source-system execution

## Users and Jobs

| User | Core job | Design response |
| --- | --- | --- |
| OCIO executive | See portfolio exposure and prioritize action | Enterprise cockpit with a small set of traceable, drillable signals |
| Vendor management lead | Prepare renew, compete, consolidate, remediate, or exit decisions | Priority worklists that connect contracts, owners, dependencies, evidence, and next actions |
| Sourcing and procurement | Understand commercial context before negotiations or sourcing events | Vendor and contract evidence with controlled access to sensitive terms |
| Technology or product owner | Assess operational impact and alternatives | Relationship paths across vendor, service, application, product, and owner |
| Finance analyst | Explain actual versus forecast variance | Reconciled variance drivers, drill-down, and source-aware calculations |
| Third-party risk analyst | Assess exposure and escalation needs | Risk evidence, concentration context, control signals, and limitations |
| Data steward | Resolve semantic and data-quality issues | Stewardship queue for identity, mapping, freshness, and definition exceptions |

## Information Architecture

| Area | Primary purpose | Essential behavior |
| --- | --- | --- |
| Executive Cockpit | Start portfolio review | Displays KPI tiles, renewal worklist, alerts, and consistent filters with as-of dates |
| Ask Vendor Intelligence | Ask a governed decision question | Applies entitlements, decomposes the question, retrieves approved evidence, validates findings, and returns a cited answer |
| Vendor 360 | Investigate one supplier | Connects commercial, spend, workforce, technology, risk, performance, relationships, and timeline context |
| Renewals | Prioritize imminent or risky renewals | Ranks work using contract timing, value, critical dependencies, owner, and explanation |
| Risk and Dependencies | Understand concentration and operational exposure | Shows governed relationship paths and confidence, without inventing links |
| Spend and Forecast | Explain financial movement | Reconciles actuals, forecast, variance drivers, and supporting records |
| Scenarios | Explore approved changes | Compares a baseline with non-destructive scenarios and discloses assumptions and impacts |
| Alerts | Surface material change | Routes users to an explainable underlying analysis rather than presenting opaque notifications |
| Administration | Govern the experience | Manages roles, thresholds, source health, metric definitions, routes, and telemetry for authorized users |

## Target Decision Journeys

### Renewal Prioritization

1. A vendor management lead opens the cockpit and filters to their portfolio.
2. The renewal worklist presents contracts approaching expiry with value, critical dependencies, owner, and priority rationale.
3. The lead opens Vendor 360 to inspect connected evidence, source freshness, and unresolved data-quality issues.
4. The lead asks a scoped question or creates a non-destructive scenario to compare options.
5. The product returns ranked options with evidence, assumptions, confidence, policy checks, and required approvals.
6. The lead exports or saves an analysis for review; no source-system change is executed.

### Explainable Vendor Question

1. An authorized user asks a business question, such as which vendors have contracts expiring soon and support critical applications.
2. The system checks entitlement scope before any retrieval.
3. The orchestration layer plans approved analytics, graph, document, and temporal tools.
4. The response returns a direct answer, findings, evidence excerpts, applied filters, relationship or calculation explanation, limitations, and recommended next actions.
5. A user can inspect each cited record, narrow the scope, or open a related Vendor 360 view.

### Scenario Comparison

1. A user selects a vendor, baseline, and an approved decision lever such as spend, workforce, or supplier change.
2. The scenario engine calculates deltas without writing to a source system.
3. The user compares baseline and scenario impacts across financial, dependency, risk, and policy dimensions.
4. The decision record makes assumptions, unknowns, and approvals required explicit.

## Interaction and Content Design

Every answer surface uses a consistent structure:

1. Direct answer or executive summary
2. Ranked findings and drivers
3. Evidence panel with source, record, relevant excerpt or value, and as-of date
4. Scope, filters, and entitlements applied
5. Calculation, temporal comparison, or relationship-path explanation
6. Assumptions, conflicts, missing data, and limitations
7. Recommended next actions labelled as recommendations
8. Feedback, save, and authorized export controls

Vendor 360 uses a stable header with identity, parent, status, category, risk tier, owners, and as-of date. Its tabs preserve the same evidence pattern across Commercial, Spend, Workforce, Technology, Risk and Performance, Relationships, and Timeline.

## Functional Architecture

| Layer | Responsibility | Key guardrail |
| --- | --- | --- |
| Experience | Cockpit, question flow, Vendor 360, graph, scenarios, alerts, and exports | Accessible, keyboard-operable controls; risk is never color-only |
| Decision orchestration | Intent routing, query planning, tool selection, policy checks, and response assembly | Cannot bypass authorization or turn advice into approval |
| Semantic and analytics | Governed metrics, SQL, graph traversal, retrieval, and temporal comparison | Uses approved definitions and declares snapshot or effective dates |
| Knowledge processing | Ingests, classifies, extracts, resolves, validates, indexes, and tracks lineage | Routes unresolved identity and quality issues to stewardship |
| Data foundation | Curated analytics, graph projection, document index, metadata, and source lineage | Separates authoritative data from illustrative demo data |
| AI runtime and controls | Model routing, prompt controls, guardrails, traces, evaluation, and cost telemetry | Prevents unsupported assertions and preserves auditability |
| Security and operations | Identity, policy enforcement, secrets, monitoring, incidents, and change control | Enforces row, attribute, document, and index-level entitlements |

## Decision Tool Boundaries

| Tool | Permitted responsibility | Must not do |
| --- | --- | --- |
| Query planner | Break down a request into approved steps | Invent data or bypass authorization |
| SQL analytics | Return approved metrics and aggregations | Run unrestricted queries against raw production sources |
| Graph analysis | Return governed entity relationships and paths | Infer unsupported relationships without confidence and evidence |
| Document retrieval | Retrieve entitled contract and policy passages | Return inaccessible documents or unsupported conclusions |
| Temporal analysis | Compare snapshots and explain change | Mix dates or snapshots without disclosure |
| Scenario engine | Apply approved levers and calculate impact | Write changes to source systems |
| Validation service | Check reconciliation, coverage, policy, and contradiction | Suppress material limitations |
| Explanation service | Assemble rationale, evidence, assumptions, and confidence | Represent generated advice as authoritative approval |

## Data, Trust, and Governance Design

The vendor identifier is the critical cross-domain control point. Before a Vendor 360 is treated as decision-ready, the design needs an entity-resolution status, source freshness, data-quality coverage, and steward owner.

Each fact must retain its source, effective date, as-of date, confidence, entitlement classification, and relationship provenance. Every metric needs a governed definition, calculation path, and reconciliation status. Sensitive commercial, risk, financial, and document evidence must be checked before retrieval and remain protected in summaries and exports.

## Candidate Solution Directions

These are FSD-derived hypotheses, not validated Method 4 concepts.

| Direction | Core idea | Primary assumption to test |
| --- | --- | --- |
| Decision Workbench | A cockpit and worklists lead users from prioritized signal to evidence-backed action | Users prefer structured decision starting points over open-ended search |
| Evidence-First Answer | Natural-language questions always return inspectable, scoped evidence and limitations | Evidence visibility improves trust without overloading users |
| Relationship Explorer | Vendor, contract, workforce, application, product, and ownership paths expose impact | Users can understand material dependencies through a governed graph view |
| Scenario Studio | Users compare approved, non-destructive changes against a baseline | Scenario comparison changes decision quality before action is taken |

## Design Thinking Completion Plan

| Method | Current position | Required evidence or outcome |
| --- | --- | --- |
| 1. Scope Conversations | Initial FSD-derived scope prepared | Confirm decision priority, stakeholder accountability, fixed constraints, and missing stakeholders |
| 2. Design Research | Not performed | Interviews and workflow observations across executive, vendor management, sourcing, technology, finance, risk, and stewardship roles |
| 3. Input Synthesis | Not performed | Multi-source themes, evidence-backed problem definition, and HMW questions |
| 4. Brainstorming | Not performed | At least 15 rough ideas spanning several solution philosophies before convergence |
| 5. User Concepts | Not performed | Two or three understandable concept cards evaluated for desirability, feasibility, and viability |
| 6. Lo-Fi Prototypes | Not performed | Scrappy evidence and scenario prototypes tested with real users in their work context |
| 7. Hi-Fi Prototypes | Not performed | Functional, real-data technical proofs and comparison of implementation approaches |
| 8. User Testing | Not performed | Task-based testing, behavioral observations, and go, iterate, or revisit decisions |
| 9. Iteration at Scale | Not performed | Production telemetry, phased rollout, training, adoption measures, and rollback plan |

## Research and Validation Priorities

1. Observe a recent renewal, concentration-risk, or spend-variance decision from trigger to approval.
2. Measure evidence-assembly time, source switching, identity reconciliation, and approval delay.
3. Test whether each role understands and trusts an evidence-first answer layout.
4. Validate which relationships are actually material to a decision and which are noise.
5. Confirm the threshold, scoring, and entitlement owners before any score is represented as policy.

## Prototype and Test Plan

Start with a paper or low-detail clickable flow for the renewal worklist, Vendor 360 evidence panel, and scenario comparison. Each prototype should test one assumption: prioritization comprehension, evidence trust, relationship-path understanding, or scenario usefulness.

Use task-based sessions with representative users. Capture completion, time, hesitation, workaround, evidence inspected, and the reason a user accepts, questions, or rejects a recommendation. Avoid preference-only questions.

## Success Measures

| Outcome | Initial measure | Validation source |
| --- | --- | --- |
| Faster decision preparation | Manual evidence-assembly time and time to a reviewable brief | Workflow observation and task testing |
| Greater trust | Evidence inspection rate, confidence rating with reason, and disagreement resolution time | User research and testing |
| Better decision utility | Decisions supported, renewal lead time, and action follow-through | Operational telemetry and stakeholder review |
| Control effectiveness | Entitlement violations, reconciliation exceptions, policy conflicts, and audit findings | Security, data-quality, and audit telemetry |
| Adoption | Repeat use for priority workflows, voluntary use, and workarounds | Product telemetry and field observation |

## Key Risks and Decisions

* Entity resolution, historical data, ownership criticality, contract metadata, and access granularity remain critical assumptions
* Risk thresholds and scoring logic must be owned and approved by Citi; otherwise outputs stay illustrative
* Explainability must be designed into the data and orchestration layers, not appended to generated text
* The product should degrade visibly when evidence is stale, incomplete, contradictory, or inaccessible
* Production integration and sensitive records require explicit approval and must not be implied by a demo

## Next Gate

The next credible step is Method 2 research. Recruit at least one participant from vendor management, sourcing, technology, finance, third-party risk, and data stewardship, then observe a real decision workflow before validating the candidate design directions.
