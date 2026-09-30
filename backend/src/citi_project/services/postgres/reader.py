"""Read-only table reads for StructuredQueryService.from_postgres."""

import psycopg
from psycopg import sql

from .config import PostgresConfig, sanitized
from .tables import FILE, LINE, TABLES


def read_tables(config=None, max_rows=10000, *, connect=None):
    """Return {name: (fields, [(line, row_dict)])} in CSV column and line order, as the reader role."""
    config = PostgresConfig.from_env() if config is None else config
    connect = psycopg.connect if connect is None else connect
    try:
        with connect(config.reader_dsn) as conn:
            conn.read_only = True  # defence in depth; the role itself holds SELECT only
            return {name: _read(conn, spec, max_rows) for name, spec in TABLES.items()}
    except psycopg.Error as exc:
        raise sanitized(exc) from None


def _read(conn, spec, max_rows):
    found = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s "
                         "ORDER BY ordinal_position", (spec.schema, spec.table)).fetchall()
    fields = [c for (c,) in found if c not in (LINE, FILE)]
    if not fields:
        return [], []  # rejected by the required-column check
    query = sql.SQL("SELECT {}, {} FROM {} ORDER BY {} LIMIT %s").format(
        sql.Identifier(LINE), sql.SQL(", ").join(map(sql.Identifier, fields)),
        sql.Identifier(spec.schema, spec.table), sql.Identifier(LINE))
    rows = conn.execute(query, (max_rows + 1,)).fetchall()  # one extra row trips the shared bound check
    return fields, [(row[0], dict(zip(fields, row[1:]))) for row in rows]
