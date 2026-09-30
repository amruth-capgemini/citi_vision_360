"""Read-only catalog and profile harvest. Samples stay in memory; nothing here writes anywhere.

The harvest reads through a small source interface so the same profiling runs against
PostgreSQL (``PostgresCatalogSource``, as the read-only role) or an in-memory table set.
"""

from dataclasses import dataclass
from datetime import date
import re

from psycopg import sql

from .ontology import CANONICAL_NAMESPACE, NAMESPACE_COLUMN

TECHNICAL_PREFIX = "_source_"  # loader lineage columns, not source fields
_NUMERIC = r"^-?[0-9]+(\.[0-9]+)?$"
_ISO_DATE = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
_STATS_CHUNK = 100  # columns per aggregate query; PostgreSQL allows 1664 result columns


class HarvestError(ValueError):
    """Invalid harvest input or bound. Messages never contain row contents."""


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    position: int
    data_type: str
    comment: str | None
    row_count: int
    empty_count: int
    distinct_count: int
    min_value: str | None
    max_value: str | None
    max_length: int
    numeric: bool
    dates: bool
    distinct_values: tuple[str, ...]  # in memory only; canonical namespace when the table has one
    distinct_truncated: bool

    @property
    def null_pct(self):
        return round(self.empty_count * 100 / self.row_count, 2) if self.row_count else 0.0

    @property
    def unique(self):
        """Every row has a value and no value repeats: a candidate key."""
        return self.row_count > 0 and self.empty_count == 0 and self.distinct_count == self.row_count


@dataclass(frozen=True)
class TableHarvest:
    schema: str
    table: str
    comment: str | None
    row_count: int
    primary_key: tuple[str, ...]
    columns: tuple[ColumnProfile, ...]
    sample_rows: tuple[dict, ...]  # in memory only, never persisted

    @property
    def dataset_id(self):
        return f"{self.schema}.{self.table}"

    def column(self, name):
        return next(c for c in self.columns if c.name == name)


@dataclass(frozen=True)
class HarvestResult:
    namespace: str
    schemas: dict  # schema -> comment
    tables: tuple[TableHarvest, ...]
    anchors: tuple[dict, ...]  # canonical vendor/contract keys and names from the master


def _shape(value):
    literal = lambda part: "".join("\\" + c if c in ".^$*+?{}[]\\|()" else c for c in part)
    return "^" + "".join(rf"\d{{{len(p)}}}" if p.isdigit() else literal(p) for p in re.split(r"(\d+)", value) if p) + "$"


def value_pattern(values):
    """One regular expression shape shared by every value, with digit runs generalised; else None."""
    shapes = {_shape(v) for v in values}
    if len(shapes) != 1:
        return None
    shape = shapes.pop()
    return shape if r"\d" in shape else None


def _valid_dates(profile_min, profile_max):
    try:
        date.fromisoformat(profile_min)
        date.fromisoformat(profile_max)
        return True
    except (TypeError, ValueError):
        return False


def harvest(source, schemas, *, sample_rows=0, max_distinct=1000, namespace=CANONICAL_NAMESPACE,
            max_tables=200, max_columns=500):
    """Profile every table in ``schemas``. ``sample_rows`` rows per table are kept in memory for the enricher."""
    if type(sample_rows) is not int or not 0 <= sample_rows <= 50:
        raise HarvestError("sample_rows must be between 0 and 50")
    if type(max_distinct) is not int or not 1 <= max_distinct <= 10000:
        raise HarvestError("max_distinct must be between 1 and 10000")
    comments = source.schemas(list(schemas))
    tables = []
    for schema in schemas:
        if schema not in comments:
            continue
        for table, comment in source.tables(schema):
            if len(tables) >= max_tables:
                raise HarvestError("Table limit exceeded")
            declared = [c for c in source.columns(schema, table) if not c[0].startswith(TECHNICAL_PREFIX)]
            if len(declared) > max_columns:
                raise HarvestError("Column limit exceeded")
            names = [c[0] for c in declared]
            row_count, stats = source.stats(schema, table, names)
            scoped = NAMESPACE_COLUMN if NAMESPACE_COLUMN in names else None
            columns = []
            for (name, data_type, position, column_comment), stat in zip(declared, stats):
                empty, distinct, low, high, length, numeric, dates = stat
                values = source.distinct(schema, table, name, max_distinct + 1, scoped, namespace)
                columns.append(ColumnProfile(
                    name, position, data_type, column_comment, row_count, empty, distinct, low, high, length,
                    bool(numeric) and distinct > 0, bool(dates) and distinct > 0 and _valid_dates(low, high),
                    tuple(values[:max_distinct]), len(values) > max_distinct))
            samples = source.sample(schema, table, names, sample_rows) if sample_rows else []
            tables.append(TableHarvest(schema, table, comment, row_count, tuple(source.primary_key(schema, table)),
                                       tuple(columns), tuple(samples)))
    return HarvestResult(namespace, {s: comments[s] for s in schemas if s in comments}, tuple(tables),
                         tuple(source.anchors(namespace)))


class PostgresCatalogSource:
    """Catalog and profile queries over a read-only psycopg connection. Identifiers are always quoted."""

    def __init__(self, conn):
        self.conn = conn

    def _all(self, query, params=()):
        return self.conn.execute(query, params).fetchall()

    def schemas(self, names):
        return dict(self._all("SELECT n.nspname, obj_description(n.oid, 'pg_namespace') FROM pg_namespace n "
                              "WHERE n.nspname = ANY(%s)", (names,)))

    def tables(self, schema):
        return self._all("SELECT c.relname, obj_description(c.oid, 'pg_class') FROM pg_class c "
                         "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s "
                         "AND c.relkind IN ('r', 'p', 'v', 'm') ORDER BY c.relname", (schema,))

    def columns(self, schema, table):
        return self._all("SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnum, col_description(a.attrelid, a.attnum) "
                         "FROM pg_attribute a WHERE a.attrelid = format('%%I.%%I', %s::text, %s::text)::regclass "
                         "AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum", (schema, table))

    def primary_key(self, schema, table):
        return [r[0] for r in self._all(
            "SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = format('%%I.%%I', %s::text, %s::text)::regclass AND i.indisprimary "
            "ORDER BY array_position(i.indkey::int2[], a.attnum)", (schema, table))]

    @staticmethod
    def stats_query(schema, table, names):
        parts = [sql.SQL("count(*)")]
        for name in names:
            value = sql.SQL("{}::text").format(sql.Identifier(name))
            present = sql.SQL("nullif({}, '')").format(value)
            parts += [sql.SQL("count(*) FILTER (WHERE {} IS NULL)").format(present),
                      sql.SQL("count(DISTINCT {})").format(present),
                      sql.SQL('min({} COLLATE "C")').format(present), sql.SQL('max({} COLLATE "C")').format(present),
                      sql.SQL("coalesce(max(length({})), 0)").format(value),
                      sql.SQL("coalesce(bool_and({} ~ {}), false)").format(present, sql.Literal(_NUMERIC)),
                      sql.SQL("coalesce(bool_and({} ~ {}), false)").format(present, sql.Literal(_ISO_DATE))]
        return sql.SQL("SELECT {} FROM {}").format(sql.SQL(", ").join(parts), sql.Identifier(schema, table))

    def stats(self, schema, table, names):
        row_count, stats = 0, []
        for start in range(0, max(len(names), 1), _STATS_CHUNK):
            chunk = names[start:start + _STATS_CHUNK]
            row = self.conn.execute(self.stats_query(schema, table, chunk)).fetchone()
            row_count = row[0]
            stats += [tuple(row[1 + 7 * i:8 + 7 * i]) for i in range(len(chunk))]
        return row_count, stats

    def distinct(self, schema, table, name, limit, scoped_column, namespace):
        value = sql.SQL("{}::text").format(sql.Identifier(name))
        where = sql.SQL("nullif({}, '') IS NOT NULL").format(value)
        params = []
        if scoped_column:
            where = sql.SQL("{} AND {} = %s").format(where, sql.Identifier(scoped_column))
            params.append(namespace)
        # Byte order, not the database collation, so value sets and truncation match everywhere.
        query = sql.SQL('SELECT DISTINCT {} COLLATE "C" FROM {} WHERE {} ORDER BY 1 LIMIT %s').format(value, sql.Identifier(schema, table), where)
        return sorted(r[0] for r in self._all(query, (*params, limit)))

    def sample(self, schema, table, names, limit):
        order = sql.Identifier(TECHNICAL_PREFIX + "line") if self._has_line(schema, table) else sql.SQL("1")
        query = sql.SQL("SELECT {} FROM {} ORDER BY {} LIMIT %s").format(
            sql.SQL(", ").join(sql.SQL("{}::text").format(sql.Identifier(n)) for n in names), sql.Identifier(schema, table), order)
        return [dict(zip(names, row)) for row in self._all(query, (limit,))]

    def _has_line(self, schema, table):
        return any(c[0] == TECHNICAL_PREFIX + "line" for c in self.columns(schema, table))

    def anchors(self, namespace):
        return [{"vendor_id": v, "vendor_name": n, "contract_id": c, "contract_description": d} for v, n, c, d in self._all(
            'SELECT "Vendor_ID", "Vendor_Name", "Contract_ID", "Contract_Description" FROM "clm"."canonical_vendor_master" '
            'WHERE "Graph_Namespace" = %s ORDER BY "Vendor_ID"', (namespace,))]
