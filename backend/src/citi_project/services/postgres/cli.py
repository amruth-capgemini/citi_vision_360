"""citi-pg: explicit init, load and verify against CITI_PG_DSN (owner role)."""

import argparse
import json
from pathlib import Path

import psycopg

from ...env import load_env
from .config import PostgresConfig, PostgresConfigurationError, PostgresOperationError, sanitized
from .loader import LoadError, init, load, verify

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[5] / "initial_plan"  # repo root, beside backend/


def main(argv=None, *, connect=None):
    parser = argparse.ArgumentParser(description="Load the synthetic structured sources into PostgreSQL")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Create schemas, the citi_reader role and the load manifest")
    for name, text in (("load", "Idempotently load every CSV in one transaction"), ("verify", "Compare row counts and SHA-256 digests with the CSVs")):
        command = commands.add_parser(name, help=text)
        command.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
        if name == "load":
            command.add_argument("--force", action="store_true", help="Reload tables even when unchanged")
    args = parser.parse_args(argv)
    if argv is None:  # a real invocation; tests pass argv and keep their own environment
        load_env()
    try:
        dsn = PostgresConfig.from_env().owner_dsn
        with (connect or psycopg.connect)(dsn, autocommit=True) as conn:
            if args.command == "init":
                result = init(conn)
            elif args.command == "load":
                result = load(conn, args.data_dir, force=args.force)
            else:
                result = verify(conn, args.data_dir)
        print(json.dumps(result, indent=2))
        return 0 if result.get("valid", True) else 1
    except (PostgresConfigurationError, PostgresOperationError, LoadError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    except psycopg.Error as exc:
        parser.exit(2, f"Error: {sanitized(exc)}\n")


if __name__ == "__main__":
    raise SystemExit(main())
