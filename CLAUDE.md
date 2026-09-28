# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

Early scaffold following the phased plan in `instructions/` (README + `00`..`10`). Do one phase at a time and stop at its gate.

- Phase 1 (done): ontology as code in `ontology/*.yaml` (`_schema.yaml` is the module JSON Schema), loaded by `src/citi_project/services/ontology/` (`OntologyRegistry`). Contract is a business entity, not a document class; `ContractDocument` EVIDENCES it. Relation direction follows the source register (e.g. `HAS_CONTRACT` is a synonym of `PARTY_TO`, Vendor → Contract). Relation assertion metadata (status, effective dates, evidence) is stored per assertion, not declared per relation.
- `src/citi_project/__init__.py` holds a `main()` stub (the `citi-project` script). `backend/requirements.txt` and `frontend/` are empty.
- Tests: `uv run pytest` (pytest is a dev dependency). The data-pack conformance tests skip when `initial_plan/` is absent. No linter/formatter yet.

## Tooling

- Python 3.13 (`.python-version`), managed with **uv** (build backend `uv_build`).
- `uv sync`: install the project into `.venv`
- `uv run citi-project`: run the entry point
- `uv add <pkg>`: add a dependency to `pyproject.toml`
- `backend/citi/` is a separate uv-created virtualenv (Python 3.13) for the backend; it is not source code. When it is active (`VIRTUAL_ENV=backend/citi`), `uv run` ignores it and uses `.venv`; pass `--active` to target it instead.
- Shell is Windows PowerShell 5.1: no `&&`; use `;` or `if ($?) { ... }`.

## What is being built (from `initial_plan/`)

`initial_plan/` and `.env` are git-ignored, so they exist locally only. They hold the design intent:

- `initial_plan/citi/citi/.copilot-tracking/dt/citi-vendor-decision-intelligence/`: the preliminary design doc, the semi-agentic solution architecture, scope, and assumptions. The FSD, decks, and source-to-target workbook are alongside as .docx/.pptx/.xlsx.
- **Architecture**: a read-only, semi-agentic decision-support platform. A **Decision Supervisor** plans a bounded investigation and delegates only to four specialists: **Commercial** (contracts/renewals), **Financial** (spend/forecast/variance/scenarios), **Dependency & Risk** (apps, workforce, concentration, TPRM), and **Sourcing** (overlap/alternatives). Specialists call governed tools (approved SQL/metrics, relationship graph, entitled document retrieval, temporal comparison, non-destructive scenario engine) and return findings through a shared **evidence contract**: source, record, excerpt/value, as-of date, calculation, assumption, confidence, limitation. Findings then go through validation (freshness, reconciliation, contradiction, entitlement) → explanation → synthesis → a decision brief for **human approval**. Agents never write back to source systems. Recommendations are withheld or degraded when evidence is missing, stale, or not entitled.
- `initial_plan/var_agent_investigation_framework.md`: a reference pattern for graph-driven investigations. Nodes/edges are defined in YAML, validated, and loaded into a NetworkX `MultiDiGraph`. A common lifecycle is combined with templates, evidence-gated branches, ranked traversal, quantitative reconciliation, and a full audit trail. Graph connectivity is never proof of causality.

## Synthetic data pack

`initial_plan/Synthetic_20_Vendor_Six_Source_Data_Pack (3)/data/`: 20 fictional vendors (V-001..V-020), with a snapshot date of 2026-09-28. Start at `vendor_catalog.json`, `data_dictionary.json`, and `source_classes_and_agent_mapping.json`.

- Six source classes in folders `01_vendor_ownership` … `06_risk_sla_performance`. Each vendor has one JSON per class; contracts, dependencies, and SLA/risk also have a PDF plus a Markdown rendition.
- Contract, service, assignment, and evidence IDs join across classes.
- Rules: amounts are **integer USD cents**. Finance covers the closed 2025 calendar year; SLA observations cover Jun–Aug 2026. Never add 2026 PO commitments or proposed service credits to 2025 actuals. Load **either** the PDF or the Markdown for each document, not both. Catalogs and rules are metadata, not extra data rows.
- `golden_questions_and_expected_answers.json` is the test oracle. **Do not index it as business evidence.**
- Demo cases: V-001 (expiring, overspend, SLA breach); V-001/V-009 (overlap, stale risk assessment); V-005 (overdue notice); V-013 (consuming an app ≠ operating it); V-017 (missing assessment; do not invent a risk tier).
