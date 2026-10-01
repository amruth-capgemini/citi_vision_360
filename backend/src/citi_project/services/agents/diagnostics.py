"""Allowlisted diagnostics: never emit exception payloads or model values."""

import logging

log = logging.getLogger("citi_project.services.agents")

REASONS = {
    "Model output failed its approved schema": "schema_validation",
    "Model entity mention was not present in the question": "entity_not_mentioned",
    "Invalid specialist selection": "specialist_selection",
    "Tool is not permitted for this specialist": "tool_permission",
    "Tool vendor was not deterministically resolved": "vendor_not_resolved",
    "A vendor-scoped request cannot silently expand to a portfolio": "vendor_scope_missing",
    "Explicit organization scope must be preserved": "organization_scope",
    "Tool scope was not supplied by the user": "invented_scope",
    "Tool parameter was not supplied by the user": "invented_parameter",
    "Workforce changes require an explicit hypothetical scenario": "scenario_not_requested",
    "Scenario category was not supplied by the user": "scenario_category",
    "Assignment ID was not supplied by the user": "assignment_not_requested",
    "Count scenario has conflicting inputs": "scenario_conflict",
    "Assignment count was not supplied by the user": "scenario_count",
    "Scenario percentage must be explicit": "scenario_percentage",
    "Baseline cannot change assignments": "baseline_change",
    "Request exceeds the tool-call bound": "tool_call_bound",
    "Planned tools do not cover all resolved vendors": "vendor_coverage",
    "Duplicate tool calls are not permitted": "duplicate_calls",
}


def failure(exc):
    log.warning("agent_validation_failure category=%s", REASONS.get(str(exc), "other_boundary_failure"))
