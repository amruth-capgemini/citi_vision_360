"""LangGraph Studio entry point for `langgraph dev` (see langgraph.json).

Builds the live exploring supervisor from the environment (PostgreSQL reader role,
the Aura business graph and catalog, Azure OpenAI) and exposes its compiled graph.
Input in Studio: {"question": "...", "active_vendor_id": null}. Everything stays read-only.
"""

import logging

from citi_project.services.agents.cli import build_live  # absolute: langgraph dev loads this file by path

logging.getLogger("neo4j").setLevel(logging.ERROR)  # driver notices about labels the model guessed
supervisor, close = build_live()
graph = supervisor.orchestrator.graph
