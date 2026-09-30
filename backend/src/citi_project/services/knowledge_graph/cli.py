"""Small explicit CLI; offline commands never initialize a Neo4j client."""

import argparse
from dataclasses import asdict
import json
import logging
from pathlib import Path

from ...env import load_env
from ..ontology import OntologyRegistry, OntologyError
from .client import GraphConnectionError
from .transport import create_client
from .config import GraphConfigurationError, Neo4jConfig
from .mapping import GraphMapper
from .models import GraphInputError, GraphPayload
from .service import KnowledgeGraphService


def sample_payload(registry):
    """Synthetic structured data only; no PDFs, source documents or inferred approvals."""
    doc = {"class_id": "ContractDocument", "identity": "V-001_contract_sow"}
    contract = {"class_id": "Contract", "identity": "CTR-001"}
    clause = {"class_id": "RenewalClause", "contract_identity": "CTR-001", "occurrence_id": "renewal-section-1"}
    return {
        "namespace": "kg-example", "ontology_version": registry.ontology_version,
        "entities": [
            {"class_id": "Vendor", "properties": {"vendor_id": "V-001", "legal_name": "Example Supplier",
                                                   "status": "Active", "vendor_type": "Technical"}},
            {"class_id": "Contract", "properties": {"contract_id": "CTR-001", "start_date": "2025-01-01",
                                                      "end_date": "2026-12-31", "pricing_model": "Fixed managed service fee"}},
            {"class_id": "ContractDocument", "properties": {"document_id": "V-001_contract_sow"}},
            {**clause, "properties": {"automatic_renewal": True, "notice_days": 60},
             "provenance": [{"document": doc, "property_id": "notice_days", "source_ref": "synthetic-example",
                             "evidence_ref": "synthetic-example/renewal-1", "review_state": "unreviewed"}]},
        ],
        "relationships": [
            {"type": "PARTY_TO", "source": {"class_id": "Vendor", "identity": "V-001"}, "target": contract},
            {"type": "HAS_CLAUSE", "source": contract, "target": clause},
            {"type": "EVIDENCED_BY", "source": contract, "target": doc},
            {"type": "EVIDENCED_BY", "source": clause, "target": doc},
        ],
    }


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise GraphInputError("Duplicate JSON object key")
        result[key] = value
    return result


def _invalid_constant(_):
    raise GraphInputError("Non-finite JSON number")


def read_payload(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=_invalid_constant)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise GraphInputError("Unable to read a valid UTF-8 JSON payload") from None
    return GraphPayload.from_dict(value)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Ontology-driven Neo4j Knowledge Graph")
    parser.add_argument("--ontology-dir", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Explicitly check configured database connectivity")
    commands.add_parser("init-schema", help="Explicitly create idempotent constraints and indexes")
    commands.add_parser("describe", help="Offline registry mapping and identity inventory")
    sample = commands.add_parser("sample", help="Write or print synthetic structured example (offline)")
    sample.add_argument("--output", type=Path)
    ingest = commands.add_parser("ingest")
    ingest.add_argument("payload", type=Path)
    ingest.add_argument("--dry-run", action="store_true", help="Offline mapping only; existing endpoints not checked")
    validate = commands.add_parser("validate", help="Read-only namespace validation")
    validate.add_argument("--namespace", required=True)
    validate.add_argument("--expected", type=Path)
    validate.add_argument("--exact", action="store_true")
    args = parser.parse_args(argv)
    if argv is None:  # a real invocation; tests pass argv and keep their own environment
        load_env()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        registry = OntologyRegistry.load(args.ontology_dir)
        if args.command == "sample":
            content = json.dumps(sample_payload(registry), indent=2) + "\n"
            if args.output:
                args.output.write_text(content, encoding="utf-8")
            else:
                print(content, end="")
            return 0
        if args.command == "describe":
            print(json.dumps({"ontology_version": registry.ontology_version,
                              "classes": {cid: {"label": cid, "abstract": c.abstract, "key": c.key,
                                  "ancestors": registry.ancestors(cid),
                                  "identity_strategy": "namespace + contract_identity + class + occurrence_id" if c.kind == "clause"
                                  else "namespace + class + ontology key",
                                  "properties": {k: asdict(v) for k, v in registry.properties_for_class(cid).items()}}
                                  for cid, c in sorted(registry.classes.items())},
                              "relationships": {rid: asdict(r) for rid, r in sorted(registry.relations.items())}}, indent=2))
            return 0
        payload = read_payload(args.payload) if args.command == "ingest" else None
        if payload is not None:
            mapped = GraphMapper(registry).map(payload)
            if args.dry_run:
                print(json.dumps({"entities": len(mapped.nodes), "relationships": len(mapped.edges),
                                  "ontology_version": mapped.ontology_version, "database_checked": False}))
                return 0
        expected = read_payload(args.expected) if args.command == "validate" and args.expected else None
        if args.command == "validate" and args.exact and expected is None:
            raise GraphInputError("--exact requires --expected")
        with create_client(Neo4jConfig.from_env()) as client:
            service = KnowledgeGraphService(registry, client)
            if args.command == "check":
                result = {"connected": client.check_connectivity()}
            elif args.command == "init-schema":
                result = {"schema_statements": service.ensure_schema()}
            elif args.command == "ingest":
                result = asdict(service.ingest(payload))
            else:
                report = service.validate(args.namespace, expected, exact=args.exact)
                result = {"valid": report.valid, **asdict(report)}
                print(json.dumps(result, indent=2))
                return 0 if report.valid else 1
            print(json.dumps(result, indent=2))
        return 0
    except (GraphInputError, GraphConfigurationError, GraphConnectionError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    except OntologyError:
        parser.exit(2, "Error: ontology could not be loaded; validate ontology configuration\n")
    except OSError:
        parser.exit(2, "Error: file operation failed\n")


if __name__ == "__main__":
    raise SystemExit(main())
