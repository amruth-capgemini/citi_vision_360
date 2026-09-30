"""citi-catalog: harvest Postgres metadata into the Neo4j catalog namespace, and describe it."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path

import psycopg

from ...env import load_env
from ..agents import ModelError, select_model
from ..knowledge_graph.client import GraphConnectionError
from ..knowledge_graph.config import GraphConfigurationError, Neo4jConfig
from ..knowledge_graph.mapping import GraphMapper
from ..knowledge_graph.models import GraphInputError
from ..knowledge_graph.service import KnowledgeGraphService
from ..knowledge_graph.transport import create_client
from ..knowledge_graph.validation import topology_report
from ..ontology import OntologyError, OntologyRegistry
from ..postgres.config import PostgresConfig, PostgresConfigurationError, PostgresOperationError, sanitized
from ..postgres.tables import SYSTEMS
from .builder import build_payload, summarize
from .classifier import Classifier
from .enricher import CatalogEnricher
from .harvester import HarvestError, PostgresCatalogSource, harvest
from .ontology import CATALOG_NAMESPACE, load_catalog_registry
from .query import CatalogQueryService


def _harvest(args):
    with psycopg.connect(PostgresConfig.from_env().reader_dsn) as conn:
        conn.read_only = True
        return harvest(PostgresCatalogSource(conn), list(SYSTEMS), sample_rows=args.sample_rows)


def _payload(args, catalog):
    business = OntologyRegistry.load()
    result = _harvest(args)
    classifier = Classifier(business)
    classification = classifier.classify(result)
    failures = []
    if not args.no_llm:
        # Column descriptions are long outputs: larger output bound and smaller batches than routing.
        model = select_model(max_output_tokens=4096, timeout_seconds=90)
        try:
            classification, failures = CatalogEnricher(model, business, classifier, columns_per_call=25).enrich(result, classification)
        finally:
            model.close()
    return build_payload(result, classification, catalog, business, namespace=args.namespace), failures


RESET_NODES = "// citi-catalog:reset\nMATCH (n:CitiKGEntity {_kg_namespace: $namespace}) DETACH DELETE n RETURN count(n) AS deleted"
RESET_SCOPE = "// citi-catalog:reset-scope\nMATCH (s:CitiKGScope {namespace: $namespace}) DELETE s RETURN count(s) AS deleted"


def reset_namespace(client, namespace, *, confirmed):
    """Delete a catalog namespace only. The catalog is derived metadata and is rebuilt by harvest.

    Needed when the catalog ontology changes, because a namespace stays pinned to one ontology_version.
    """
    if not confirmed:
        raise GraphInputError("reset deletes the catalog namespace; pass --yes to confirm")
    if not namespace.startswith("catalog-"):
        raise GraphInputError("reset only deletes catalog-* namespaces")
    nodes = client.write(lambda tx: tx.run(RESET_NODES, namespace=namespace).single()["deleted"])
    scopes = client.write(lambda tx: tx.run(RESET_SCOPE, namespace=namespace).single()["deleted"])
    return {"namespace": namespace, "deleted_nodes": nodes, "deleted_scopes": scopes}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Metadata catalog harvested from PostgreSQL into Neo4j")
    parser.add_argument("--namespace", default=CATALOG_NAMESPACE)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-schema", help="Create the catalog ontology's Neo4j constraints and indexes")
    run = commands.add_parser("harvest", help="Harvest, classify, optionally enrich, and ingest the catalog")
    run.add_argument("--dry-run", action="store_true", help="Build and validate the payload offline; Neo4j is not contacted")
    run.add_argument("--no-llm", action="store_true", help="Deterministic classification only; no model calls")
    run.add_argument("--sample-rows", type=int, default=0,
                     help="Masked rows per table shown to the model. Default 0: the model sees profiles only")
    run.add_argument("--output", type=Path, help="Also write the payload JSON to this file")
    describe = commands.add_parser("describe", help="Show datasets, mappings and review states from the catalog")
    describe.add_argument("--dataset", help="schema.table to show field by field")
    describe.add_argument("--contract", help="CTR-### to list the datasets holding its records")
    describe.add_argument("--join-plan", metavar="CONCEPT", help="Class.property, e.g. Contract.end_date: key home and joining datasets")
    reset = commands.add_parser("reset", help="Delete the catalog namespace (derived data; rebuild with harvest)")
    reset.add_argument("--yes", action="store_true", help="Confirm deletion of the catalog namespace")
    args = parser.parse_args(argv)
    if argv is None:  # a real invocation; tests pass argv and keep their own environment
        load_env()
    try:
        catalog = load_catalog_registry()
        if args.command == "harvest":
            payload, failures = _payload(args, catalog)
            if args.output:
                args.output.write_text(json.dumps(asdict(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            summary = {**summarize(payload), "enrichment": "skipped" if args.no_llm else {"failed_datasets": failures}}
            if args.dry_run:
                mapped = GraphMapper(catalog).map(payload)
                report = topology_report(catalog, list(mapped.nodes), list(mapped.edges))
                print(json.dumps({**summary, "valid": report.valid, "database_checked": False,
                                  "issues": [asdict(i) for i in report.issues]}, indent=2))
                return 0 if report.valid else 1
        with create_client(Neo4jConfig.from_env()) as client:
            service = KnowledgeGraphService(catalog, client)
            if args.command == "init-schema":
                print(json.dumps({"schema_statements": service.ensure_schema()}))
                return 0
            if args.command == "describe":
                query = CatalogQueryService(client, args.namespace)
                result = (query.describe_dataset(args.dataset) if args.dataset
                          else query.datasets_for_contract(args.contract) if args.contract
                          else query.join_plan(args.join_plan) if args.join_plan else query.overview())
                print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
                return 0
            if args.command == "reset":
                print(json.dumps(reset_namespace(client, args.namespace, confirmed=args.yes)))
                return 0
            # The catalog is derived: each harvest replaces the namespace atomically, so removed
            # fields and changed mappings leave nothing stale. An identical harvest writes nothing.
            existing, nodes, edges = client.read(service.validator.snapshot, args.namespace)
            mapped = service.mapper.map(payload)
            if (existing.valid and {n.key: n for n in nodes} == {n.key: n for n in mapped.nodes}
                    and {e.key: e for e in edges} == {e.key: e for e in mapped.edges}):
                result = service.ingest(payload)
            else:
                result = service.replace(payload, namespace_prefix="catalog-")
            report = service.validate(args.namespace)
            print(json.dumps({**summary, "ingestion": asdict(result), "valid": report.valid,
                              "nodes_by_class": report.nodes_by_class, "relationships_by_type": report.relationships_by_type}, indent=2))
            return 0 if report.valid else 1
    except (GraphInputError, GraphConfigurationError, GraphConnectionError, HarvestError,
            PostgresConfigurationError, PostgresOperationError, ModelError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    except psycopg.Error as exc:
        parser.exit(2, f"Error: {sanitized(exc)}\n")
    except OntologyError:
        parser.exit(2, "Error: ontology could not be loaded; validate ontology configuration\n")


if __name__ == "__main__":
    raise SystemExit(main())
