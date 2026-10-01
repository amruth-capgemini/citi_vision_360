"""Idempotent CSV-to-PostgreSQL load. SQL is composed from identifiers and bound values only."""

import csv
import hashlib
import io
import json
from pathlib import Path

from psycopg import sql

from .tables import FILE, LINE, META_SCHEMA, READER_ROLE, SYSTEMS, TABLES

MAX_IDENTIFIER_BYTES = 63
MANIFEST = sql.Identifier(META_SCHEMA, "load_manifest")
_LINEAGE_LABELS = {
    "canonical_source_columns": "canonical source column",
    "canonical_or_source_derived_columns": "canonical or source-derived column",
    "source_derived_or_reference_columns": "source-derived or reference column",
    "synthetic_or_scenario_metadata_columns": "synthetic or scenario metadata",
    "synthetic_only_columns": "synthetic-only column",
    "retained_columns": "retained from the original extract",
    "added_columns": "added during regeneration",
}


class LoadError(ValueError):
    """Invalid source file. Messages name the table, never row contents."""


def read_csv(path, name):
    """Return (fields, [(line, values)], file_sha256). Lines match csv.DictReader numbering."""
    try:
        raw = Path(path).read_bytes()
        reader = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
        fields = next(reader, [])
        rows, line = [], 1
        for values in reader:
            if not values:
                continue  # DictReader skips blank rows without counting them
            line += 1
            if len(values) != len(fields):
                raise LoadError(f"Malformed row in {name}")
            rows.append((line, values))
    except (OSError, UnicodeError, csv.Error):
        raise LoadError(f"Unable to read {name}") from None
    if not fields or len(fields) != len(set(fields)):
        raise LoadError(f"Invalid columns in {name}")
    for column in fields:
        if not column or column.startswith("_source_") or len(column.encode()) > MAX_IDENTIFIER_BYTES or any(ord(c) < 32 for c in column):
            raise LoadError(f"Unsupported column name in {name}")
    return fields, rows, hashlib.sha256(raw).hexdigest()


def content_digest(fields, rows):
    """Order-sensitive digest of column names and (line, values) pairs, computable from CSV or database."""
    payload = json.dumps({"columns": list(fields), "rows": [[line, list(values)] for line, values in rows]},
                         ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def lineage_comments(data_dir):
    """Column comments from generated/schema_lineage.json, keyed by source file; empty when absent."""
    try:
        lineage = json.loads((Path(data_dir) / "generated" / "schema_lineage.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return {}
    comments = {}
    for source, groups in lineage.items() if isinstance(lineage, dict) else ():
        columns = comments.setdefault(source.replace("\\", "/"), {})
        for group, label in _LINEAGE_LABELS.items():
            for column in groups.get(group, ()):
                columns.setdefault(column, []).append(label)
        for old, new in (groups.get("renamed_or_clarified") or {}).items():
            if isinstance(new, str) and new != old and " " not in new:
                columns.setdefault(new, []).append(f"renamed from {old}")
            elif isinstance(new, str):
                columns.setdefault(old, []).append(new)
    return {source: {c: "Lineage: " + "; ".join(labels) + "." for c, labels in cols.items()} for source, cols in comments.items()}


def _qualified(spec):
    return sql.Identifier(spec.schema, spec.table)


def init_statements():
    """Schemas, reader privileges and the load manifest. The reader role itself is created by init()."""
    reader = sql.Identifier(READER_ROLE)
    statements = []
    for schema in (*SYSTEMS, META_SCHEMA):
        ident = sql.Identifier(schema)
        statements += [sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(ident),
                       sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(ident, reader),
                       sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA {} GRANT SELECT ON TABLES TO {}").format(ident, reader)]
    for schema, description in SYSTEMS.items():
        statements.append(sql.SQL("COMMENT ON SCHEMA {} IS {}").format(sql.Identifier(schema), sql.Literal(description)))
    statements += [
        sql.SQL("CREATE TABLE IF NOT EXISTS {} (table_key text PRIMARY KEY, schema_name text NOT NULL, table_name text NOT NULL, "
                "source_file text NOT NULL, file_sha256 text NOT NULL, row_count integer NOT NULL, content_sha256 text NOT NULL)").format(MANIFEST),
        sql.SQL("GRANT SELECT ON {} TO {}").format(MANIFEST, reader),
    ]
    return statements


def table_statements(spec, fields, comments):
    """DDL for one table: every CSV column as quoted text, plus source line and file for evidence."""
    table = _qualified(spec)
    columns = [sql.SQL("{} text NOT NULL").format(sql.Identifier(c)) for c in fields]
    columns += [sql.SQL("{} integer NOT NULL UNIQUE").format(sql.Identifier(LINE)), sql.SQL("{} text NOT NULL").format(sql.Identifier(FILE))]
    key = sql.SQL(", ").join(map(sql.Identifier, spec.key))
    statements = [
        sql.SQL("DROP TABLE IF EXISTS {}").format(table),
        sql.SQL("CREATE TABLE {} ({}, PRIMARY KEY ({}))").format(table, sql.SQL(", ").join(columns), key),
        sql.SQL("COMMENT ON TABLE {} IS {}").format(table, sql.Literal(
            f"{SYSTEMS[spec.schema]}. Loaded from {spec.source_file} of the synthetic data pack; values are text exactly as in the CSV.")),
        sql.SQL("COMMENT ON COLUMN {} IS {}").format(sql.Identifier(spec.schema, spec.table, LINE), sql.Literal(
            "Row number in the source CSV (header is line 1), cited by evidence.")),
        sql.SQL("COMMENT ON COLUMN {} IS {}").format(sql.Identifier(spec.schema, spec.table, FILE), sql.Literal(
            "Source CSV path relative to the data directory.")),
    ]
    for column in fields:
        if column in comments:
            statements.append(sql.SQL("COMMENT ON COLUMN {} IS {}").format(sql.Identifier(spec.schema, spec.table, column), sql.Literal(comments[column])))
    statements.append(sql.SQL("GRANT SELECT ON {} TO {}").format(table, sql.Identifier(READER_ROLE)))
    return statements


def copy_statement(spec, fields):
    return sql.SQL("COPY {} ({}) FROM STDIN").format(_qualified(spec), sql.SQL(", ").join(map(sql.Identifier, [*fields, LINE, FILE])))


def init(conn):
    with conn.transaction():
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (READER_ROLE,)).fetchone() is None:
            conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(READER_ROLE)))
        for statement in init_statements():
            conn.execute(statement)
    return {"schemas": [*SYSTEMS, META_SCHEMA], "reader_role": READER_ROLE}


def _database_state(conn, spec):
    """(columns, [(line, values)]) as stored, or None when the table is absent."""
    found = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s "
                         "ORDER BY ordinal_position", (spec.schema, spec.table)).fetchall()
    if not found:
        return None
    columns = [c for (c,) in found if c not in (LINE, FILE)]
    select = sql.SQL("SELECT {}, {} FROM {} ORDER BY {}").format(
        sql.Identifier(LINE), sql.SQL(", ").join(map(sql.Identifier, columns)),
        _qualified(spec), sql.Identifier(LINE))
    return columns, [(row[0], list(row[1:])) for row in conn.execute(select).fetchall()]


def _manifest(conn, spec):
    return conn.execute(sql.SQL("SELECT file_sha256, row_count, content_sha256 FROM {} WHERE table_key = %s").format(MANIFEST),
                        (spec.name,)).fetchone()


def load(conn, data_dir, *, force=False):
    """Load every table in one transaction. Tables whose source and content are unchanged are left untouched."""
    comments = lineage_comments(data_dir)
    sources = {name: read_csv(Path(data_dir) / spec.source_file, name) for name, spec in TABLES.items()}
    report = {}
    with conn.transaction():
        for name, spec in TABLES.items():
            fields, rows, file_hash = sources[name]
            digest = content_digest(fields, rows)
            state = None if force else _database_state(conn, spec)
            if (state is not None and _manifest(conn, spec) == (file_hash, len(rows), digest)
                    and content_digest(*state) == digest):
                report[name] = {"table": f"{spec.schema}.{spec.table}", "rows": len(rows), "action": "unchanged"}
                continue
            for statement in table_statements(spec, fields, comments.get(spec.source_file, {})):
                conn.execute(statement)
            with conn.cursor() as cur, cur.copy(copy_statement(spec, fields)) as copy:
                for line, values in rows:
                    copy.write_row([*values, line, spec.source_file])
            conn.execute(sql.SQL("INSERT INTO {} VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (table_key) DO UPDATE SET "
                                 "schema_name = EXCLUDED.schema_name, table_name = EXCLUDED.table_name, source_file = EXCLUDED.source_file, "
                                 "file_sha256 = EXCLUDED.file_sha256, row_count = EXCLUDED.row_count, content_sha256 = EXCLUDED.content_sha256").format(MANIFEST),
                         (name, spec.schema, spec.table, spec.source_file, file_hash, len(rows), digest))
            report[name] = {"table": f"{spec.schema}.{spec.table}", "rows": len(rows), "action": "loaded"}
    return report


def verify(conn, data_dir):
    """Compare row counts and SHA-256 content digests between the CSVs and the database."""
    report, ok = {}, True
    for name, spec in TABLES.items():
        fields, rows, file_hash = read_csv(Path(data_dir) / spec.source_file, name)
        state = _database_state(conn, spec)
        manifest = _manifest(conn, spec)
        checks = {"table_present": state is not None,
                  "columns_match": state is not None and state[0] == fields,
                  "row_count_match": state is not None and len(state[1]) == len(rows),
                  "content_sha256_match": state is not None and content_digest(*state) == content_digest(fields, rows),
                  "source_file_unchanged": manifest is not None and manifest[0] == file_hash}
        ok = ok and all(checks.values())
        report[name] = {"table": f"{spec.schema}.{spec.table}", "csv_rows": len(rows),
                        "database_rows": len(state[1]) if state else 0, **checks}
    return {"valid": ok, "tables": report}
