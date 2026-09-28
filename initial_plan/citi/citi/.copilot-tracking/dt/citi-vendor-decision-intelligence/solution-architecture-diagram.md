---
title: Citi Vendor Decision Intelligence Solution Architecture
description: Preliminary FSD-informed architecture for governed vendor decision support
ms.date: 2026-09-27
---

## Status

This preliminary diagram reflects the FSD and current Method 1 design work. Validate integration, entitlement, data-quality, and performance assumptions through Methods 2, 6, 7, and 8 before production implementation.

## Solution Architecture

```mermaid
flowchart TB
  users["Decision Users<br/>OCIO Executive | Vendor Management | Sourcing<br/>Technology or Product | Finance | Third-Party Risk"]

  subgraph experience["Experience Layer"]
    cockpit["Executive Cockpit"]
    ask["Ask Vendor Intelligence"]
    vendor360["Vendor 360 and Relationship Explorer"]
    scenarioUi["Scenario Studio"]
    alerts["Alerts and Authorized Exports"]
  end

  identity["Enterprise Identity and Session Controls"]
  entitlement["Role and Entitlement Policy<br/>Row | Attribute | Document | Index"]

  subgraph orchestration["Decision Orchestration"]
    planner["Intent Routing and Query Planner"]
    policy["Policy Checks"]
    response["Response Assembly"]
  end

  subgraph tools["Approved Decision Tools"]
    sql["SQL Analytics"]
    graph["Graph Analysis"]
    docs["Document Retrieval"]
    temporal["Temporal Analysis"]
    scenarios["Scenario Engine<br/>Non-destructive"]
    validation["Validation Service"]
    explanation["Explanation Service"]
  end

  subgraph control["AI Runtime and Controls"]
    gateway["Approved Model Gateway"]
    guardrails["Prompt Management and Guardrails"]
    telemetry["Trace, Evaluation, and Cost Telemetry"]
  end

  subgraph foundation["Semantic, Knowledge, and Data Foundation"]
    semantic["Governed Metrics and Semantic Layer"]
    processing["Ingest | Entity Resolution | Data Quality | Lineage"]
    stores["Curated Analytics | Graph Projection<br/>Document Index | Metadata"]
  end

  subgraph sources["Authoritative Sources"]
    contracts["Contract Lifecycle"]
    procurement["Procurement"]
    finance["ERP and Finance"]
    risk["TPRM and Risk"]
    workforce["Workforce"]
    cmdb["CMDB, Application, and Product"]
    policies["Policy Documents"]
  end

  operations["Security and Operations<br/>Secrets | SIEM and Monitoring | Incident and Change Control"]

  users --> cockpit & ask & vendor360 & scenarioUi & alerts
  cockpit & ask & vendor360 & scenarioUi & alerts --> identity --> entitlement --> planner
  planner --> policy --> sql & graph & docs & temporal & scenarios
  sql & graph & docs & temporal & scenarios --> validation --> explanation --> response
  response --> cockpit & ask & vendor360 & scenarioUi & alerts

  planner -. governed model access .-> gateway --> guardrails --> telemetry
  sql & graph & docs & temporal & scenarios --> semantic
  semantic --> processing --> stores
  contracts & procurement & finance & risk & workforce & cmdb & policies --> processing
  operations --- identity
  operations --- entitlement
  operations --- gateway
  operations --- stores

  scenarioUi -. no writeback .-> scenarios
```

## Control Boundaries

* Entitlement enforcement occurs before retrieval, generation, display, and export
* The validation and explanation services are mandatory on the response path
* The scenario engine reads governed inputs and calculates impacts; it does not change source systems
* Every user-facing decision output must carry evidence, provenance, scope, as-of date, assumptions, confidence, and limitations
* Unresolved entity mappings, freshness issues, and data-quality failures remain visible and route to stewardship

## ASCII Fallback

```text
+--------------------------------------------------------------------------------------+
| Decision Users                                                                       |
| OCIO | Vendor Management | Sourcing | Technology | Finance | Third-Party Risk       |
+----------------------------------------------+---------------------------------------+
                                               |
                                               v
+--------------------------------------------------------------------------------------+
| Experience: Cockpit | Ask | Vendor 360 | Relationship Explorer | Scenarios | Alerts |
+----------------------------------------------+---------------------------------------+
                                               |
                                               v
+--------------------------------------------------------------------------------------+
| Enterprise Identity -> Entitlement Policy -> Session Controls                        |
| Row / Attribute / Document / Index enforcement before retrieval                      |
+----------------------------------------------+---------------------------------------+
                                               |
                                               v
+--------------------------------------------------------------------------------------+
| Decision Orchestration: Intent Routing / Query Planner / Policy / Response Assembly |
+----------------------------------------------+---------------------------------------+
                                               |
                  +----------------------------+----------------------------+
                  |                                                         |
                  v                                                         v
+--------------------------------------+     +------------------------------------------+
| Approved Tools                       |     | AI Runtime and Controls                  |
| SQL | Graph | Documents | Temporal   |     | Model Gateway | Guardrails | Telemetry    |
| Scenarios | Validation | Explanation |     +------------------------------------------+
+------------------+-------------------+
                   |
                   v
+--------------------------------------------------------------------------------------+
| Semantic, Knowledge, and Data Foundation                                              |
| Governed Metrics | Entity Resolution | Data Quality | Lineage | Curated Stores       |
+----------------------------------------------+---------------------------------------+
                                               ^
                                               |
+--------------------------------------------------------------------------------------+
| Authoritative Sources: Contracts | Procurement | ERP | TPRM | Workforce | CMDB | Docs|
+--------------------------------------------------------------------------------------+

Scenarios are non-destructive and do not write to authoritative sources.
```

## Design Notes

* The workbench is decision support, not autonomous approval or a replacement for systems of record
* The vendor identity and relationship provenance are cross-domain controls that determine whether Vendor 360 is decision-ready
* Explainability is assembled from governed evidence and validation results, rather than generated independently of them
* Operations spans every layer to protect secrets, monitor access and quality, and provide audit and incident response
