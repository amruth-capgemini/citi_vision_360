"""citi-agent: ask the exploring supervisor a question against the live sources.

Wires PostgreSQL (reader role), the Neo4j business graph and catalog, and Azure OpenAI
or OpenAI from the environment. The command loads .env (variables already set win).
"""

import argparse
import json
import logging
import sys

from ...env import load_env
from .contracts import AgentError, ModelError


def build_live(*, explore=True):
    """Return (supervisor, close) wired to the configured live services."""
    live = wire_live(explore=explore)
    return live["supervisor"], live["close"]


def wire_live(*, explore=True):
    """The live supervisor plus the collaborators the API also needs (graph client, catalog, model)."""
    from ..catalog import CATALOG_NAMESPACE, CatalogQueryService
    from ..decision_intelligence import DecisionIntelligenceService
    from ..knowledge_graph.config import Neo4jConfig
    from ..knowledge_graph.query_service import KnowledgeGraphQueryService
    from ..knowledge_graph.transport import create_client
    from ..ontology import OntologyRegistry
    from ..semantic_service import VendorSemanticService
    from ..structured_data import StructuredQueryService
    from ..structured_data.query_service import NAMESPACE
    from .digest import DataDigest
    from .executors import CypherExecutor, SqlExecutor
    from .explorer import Explorer, graph_schema, pii_properties
    from .openai_model import select_model
    from .query_guard import SchemaIndex
    from .supervisor import SupervisorAgent

    registry = OntologyRegistry.load()
    client = create_client(Neo4jConfig.from_env())
    model = select_model(max_output_tokens=4096, timeout_seconds=90, max_input_chars=110000)
    structured = StructuredQueryService.from_postgres()
    graph = KnowledgeGraphQueryService(registry, client, namespace=NAMESPACE)
    decision = DecisionIntelligenceService(VendorSemanticService(structured, graph))
    explorer = None
    catalog = CatalogQueryService(client)
    if explore:
        pii = pii_properties(registry)
        explorer = Explorer(model, SchemaIndex.from_catalog(catalog), sql=SqlExecutor.from_env(),
                            cypher=CypherExecutor(client, {"business": NAMESPACE, "catalog": CATALOG_NAMESPACE}, pii_properties=pii),
                            graph_schema=graph_schema(registry), pii_properties=pii, max_payload_chars=100000)

    def close():
        model.close()
        client.close()
    return {"supervisor": SupervisorAgent(decision, model, explorer=explorer, digest=DataDigest(registry)), "close": close, "client": client,
            "catalog": catalog, "model": model}


def _summary(result):
    lines = [f"status: {result['status']}   specialists: {', '.join(result['specialists_used']) or '-'}", ""]
    for entry in result["trace"]:
        detail = {k: v for k, v in entry.items() if k not in ("node", "status", "duration_ms", "steps", "arguments")}
        lines.append(f"  {entry['node']:<13} {entry['status']:<6} {entry.get('duration_ms', 0):>6} ms  {json.dumps(detail, default=str)[:160]}")
        for s in entry.get("steps", []):
            what = s.get("query") or ", ".join(s.get("datasets") or []) or ""
            lines.append(f"      {s['node']:<16} {s.get('action', ''):<14} {s.get('purpose', ''):<11} {s.get('status', ''):<9} "
                         f"rows={s.get('row_count', '-')}  {' '.join(what.split())[:140]}")
            for key in ("thought", "message"):
                if s.get(key):
                    lines.append(f"{'':>24}{key}: {s[key][:160]}")
    lines.append("")
    lines.append("sources: " + ", ".join(f"{s['source']} ({'/'.join(s['via'])})" for s in result["sources_used"]))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="citi-agent", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    ask = sub.add_parser("ask", help="answer one question")
    ask.add_argument("question")
    ask.add_argument("--no-explore", action="store_true", help="certified tools only")
    ask.add_argument("--trace", action="store_true", help="print the node and explorer trace")
    ask.add_argument("--json", dest="json_path", help="write the full result to this file")
    args = parser.parse_args(argv)
    if argv is None:  # a real invocation; tests pass argv and keep their own environment
        load_env()
    logging.getLogger("neo4j").setLevel(logging.ERROR)  # driver notifications about labels the model guessed
    try:
        supervisor, close = build_live(explore=not args.no_explore)
    except (AgentError, ModelError, ValueError, RuntimeError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    try:
        result = supervisor.ask(args.question)
    finally:
        close()
    print(result["final_answer"])
    if args.trace:
        print("\n" + _summary(result))
    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, ensure_ascii=False, default=str)
    return 0 if result["status"] == "answered" else 1


if __name__ == "__main__":
    raise SystemExit(main())
