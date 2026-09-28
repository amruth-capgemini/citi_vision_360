---
title: Citi Vendor Decision Intelligence Semi-Agentic Solution Architecture
description: Supervisory multi-agent architecture for governed vendor decision support
ms.date: 2026-09-27
---

## Architecture Position

This is a semi-agentic decision-support platform. A supervisor coordinates bounded specialist investigations, but humans retain accountability for business decisions and any downstream action. All specialist outputs are evidence-bearing findings, not approvals or source-system commands.

## Semi-Agentic Solution Architecture

```mermaid
flowchart TB
  users["Authorized Decision Users<br/>OCIO | Vendor Management | Sourcing | Technology<br/>Finance | Third-Party Risk"]
  channels["Decision Experience<br/>Cockpit | Ask Vendor Intelligence | Vendor 360<br/>Renewals | Risk and Dependencies | Scenarios | Alerts"]
  identity["Enterprise Identity, Session, and Role Controls"]
  entitlement["Entitlement Enforcement<br/>Row | Attribute | Document | Index"]

  subgraph supervisorLayer["Supervisory Agent Layer"]
    supervisor["Decision Supervisor<br/>Classifies request, creates investigation plan,<br/>selects specialists, manages scope and budget"]
    policy["Policy and Task Guardrails<br/>Permitted actions, data scope, tool allow-list"]
    synthesis["Decision Synthesis<br/>Combines specialist findings without changing evidence"]
  end

  subgraph specialistLayer["Bounded Specialist Agents"]
    commercial["Commercial Agent<br/>Contracts, renewals, obligations, supplier terms"]
    financial["Financial Agent<br/>Spend, forecast, variance, savings calculations"]
    dependency["Dependency and Risk Agent<br/>Application, product, workforce, concentration, TPRM"]
    sourcing["Sourcing Agent<br/>Supplier overlap, alternatives, consolidation options"]
  end

  evidence["Evidence and Finding Contract<br/>Source | Record | Excerpt or Value | As-of Date<br/>Calculation | Assumption | Confidence | Limitation"]
  validator["Validation and Reconciliation Service<br/>Coverage, policy, contradiction, freshness, DQ checks"]
  explainer["Explanation Service<br/>Rationale, relationship path, calculation, limitations"]
  review["Human Review and Approval<br/>Decision owner evaluates recommendation and evidence"]
  recommendation["Decision Brief or Recommendation<br/>Options, evidence, assumptions, limitations, required approvals"]

  subgraph governedTools["Governed Data and Calculation Services"]
    sql["Approved SQL and Metric Service"]
    graph["Governed Relationship Graph"]
    retrieval["Entitled Document Retrieval"]
    temporal["Temporal Comparison Service"]
    scenario["Scenario Calculation Engine<br/>Read-only and non-destructive"]
  end

  subgraph foundation["Data and Knowledge Foundation"]
    semantic["Semantic Layer and Metric Definitions"]
    knowledge["Entity Resolution | Data Quality | Lineage | Metadata"]
    stores["Curated Analytics | Graph Projection | Document Index"]
  end

  sources["Authoritative Sources<br/>Contracts | Procurement | ERP | Workforce | CMDB<br/>Application and Product | TPRM | Policy Documents"]
  controls["Cross-Cutting Controls<br/>Model Gateway | Prompt Guardrails | Trace and Evaluation<br/>Audit Logs | Secrets | SIEM | Monitoring | Change Control"]

  users --> channels --> identity --> entitlement --> supervisor
  supervisor --> policy
  policy --> commercial & financial & dependency & sourcing
  commercial & financial & dependency & sourcing --> evidence
  evidence --> validator --> explainer --> synthesis --> recommendation --> review
  review --> channels

  commercial --> retrieval & temporal
  financial --> sql & temporal & scenario
  dependency --> graph & sql & retrieval
  sourcing --> sql & graph & retrieval & scenario
  sql & graph & retrieval & temporal & scenario --> semantic --> knowledge --> stores
  sources --> knowledge

  controls --- supervisor
  controls --- commercial
  controls --- financial
  controls --- dependency
  controls --- sourcing
  controls --- validator
  controls --- stores

  scenario -. no writeback .-> sources
```

## Agent Responsibilities

| Agent | Purpose | Permitted tools | Must not do |
| --- | --- | --- | --- |
| Decision Supervisor | Plans the investigation, delegates only relevant work, and enforces scope, policy, and cost limits | Specialist agents and approved task policy | Access raw sources, make business approvals, or turn a recommendation into an action |
| Commercial Agent | Investigates contracts, renewals, obligations, and supplier terms | Entitled document retrieval and temporal comparison | Interpret legal terms as a final legal opinion |
| Financial Agent | Explains spend, forecast, variance, and approved scenario impacts | Approved SQL, temporal comparison, scenario calculation | Change forecasts or financial records |
| Dependency and Risk Agent | Investigates operational dependency, concentration, workforce, and risk context | Governed graph, approved SQL, and entitled retrieval | Infer an unsupported relationship or make a risk acceptance decision |
| Sourcing Agent | Identifies overlap, alternatives, and consolidation options | Approved SQL, graph, retrieval, and scenario calculation | Start sourcing events, issue orders, or offboard vendors |

## Controlled Investigation Flow

1. The user asks a question or opens a prioritization workflow.
2. Identity, role, session, and row, attribute, document, and index entitlements are enforced before any retrieval.
3. The Decision Supervisor converts the request into a bounded investigation plan and selects only the relevant specialists.
4. Each specialist uses approved services and returns a structured finding with cited evidence, time context, calculations, assumptions, confidence, and limitations.
5. Validation checks entitlement coverage, freshness, reconciliation, data quality, contradictions, and policy constraints.
6. Decision Synthesis combines validated findings into options; it cannot alter or hide evidence and limitations.
7. The experience presents an evidence-backed decision brief for human review and approval.
8. No agent writes to procurement, contract, ERP, workforce, TPRM, or CMDB systems. A separately authorized, human-initiated downstream workflow is required for execution.

## Guardrails

* Only the supervisor can delegate work, and it can delegate only to the approved specialist set
* Specialists exchange evidence-bearing findings through the shared contract, not unconstrained free text
* The recommendation is withheld or clearly degraded when evidence is inaccessible, stale, contradictory, incomplete, or outside user entitlement
* Scenario calculations are non-destructive and explicitly label the baseline, lever, assumption, and time horizon
* Every investigation is traceable: user, scope, selected agent, tool calls, source references, validations, response, and human decision
* Business action is always outside the agent boundary and requires a human owner and approved operational workflow

## Why This Fits the FSD

The FSD already requires intent routing, query planning, approved tool selection, validation, explanation, evidence, limitations, and non-destructive scenarios. The semi-agentic architecture makes these capabilities explicit as a supervisor coordinating domain-focused specialists while retaining the FSD's requirement for governed retrieval and human decision authority.
