"""Read-only executors for guarded queries.

Error text returned to the explorer is short and built here: a PostgreSQL diagnostic
or a Neo4j error code and message about the model's own query. It never includes
DSNs, credentials or driver internals.
"""

from datetime import date, datetime, time
from decimal import Decimal

from .contracts import AgentError
from .query_guard import GuardedCypher, GuardedSql


class QueryFailed(AgentError):
    """The guarded query ran and failed; the message is safe to show the model."""


def _scalar(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    return str(value)


class SqlExecutor:
    """Runs guarded SQL as the read-only reader role, in a read-only transaction with a timeout."""

    def __init__(self, dsn, *, timeout_ms=5000, max_rows=50, connect=None):
        if not isinstance(dsn, str) or not dsn.strip():
            raise AgentError("A reader DSN is required for dynamic SQL")
        if type(timeout_ms) is not int or not 100 <= timeout_ms <= 30000 or type(max_rows) is not int or not 1 <= max_rows <= 500:
            raise AgentError("Invalid SQL executor bound")
        self._dsn, self.timeout_ms, self.max_rows = dsn, timeout_ms, max_rows
        self._connect = connect

    def __repr__(self):
        return f"SqlExecutor(timeout_ms={self.timeout_ms}, max_rows={self.max_rows})"

    @classmethod
    def from_env(cls, **kwargs):
        from ..postgres import PostgresConfig
        return cls(PostgresConfig.from_env().reader_dsn, **kwargs)

    def run(self, guarded):
        if not isinstance(guarded, GuardedSql):
            raise AgentError("Only guarded SQL can be executed")
        import psycopg
        try:
            conn = (self._connect or psycopg.connect)(self._dsn)
        except Exception:
            raise QueryFailed("PostgreSQL is unavailable") from None
        try:
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('statement_timeout', %s, true)", (str(self.timeout_ms),))
                # No parameters: the regenerated SQL is sent as-is, so % in literals is not interpreted.
                cur.execute(guarded.sql)
                columns = [c.name for c in cur.description or ()]
                rows = cur.fetchmany(self.max_rows + 1)
        except psycopg.Error as exc:
            diag = getattr(exc, "diag", None)
            message = (getattr(diag, "message_primary", None) or "query failed")[:300]
            raise QueryFailed(f"PostgreSQL error {exc.sqlstate or ''}: {message}".replace("  ", " ")) from None
        finally:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return {"columns": columns, "rows": [[_scalar(v) for v in row] for row in rows[:self.max_rows]],
                "truncated": len(rows) > self.max_rows}


class CypherExecutor:
    """Runs guarded Cypher in a read transaction; the host binds $namespace for the chosen graph."""

    def __init__(self, client, namespaces, *, max_rows=50, pii_properties=()):
        if not isinstance(namespaces, dict) or not namespaces or type(max_rows) is not int or not 1 <= max_rows <= 500:
            raise AgentError("Invalid Cypher executor configuration")
        self.client, self.namespaces, self.max_rows = client, dict(namespaces), max_rows
        self.pii = frozenset(pii_properties)

    def _plain(self, value):
        # Graph values first: a driver node or relationship is mapping-like but carries labels or a type.
        if hasattr(value, "nodes") and hasattr(value, "relationships"):
            return {"nodes": [self._plain(n) for n in value.nodes], "relationships": [self._plain(r) for r in value.relationships]}
        if hasattr(value, "labels") and hasattr(value, "items"):
            labels = sorted(label for label in value.labels if label != "CitiKGEntity")
            return {"labels": labels, **self._plain(dict(value.items()))}
        if hasattr(value, "start_node") and hasattr(value, "type") and hasattr(value, "items"):
            return {"type": value.type, **self._plain(dict(value.items()))}
        if isinstance(value, dict):
            return {k: self._plain(v) for k, v in value.items() if not str(k).startswith("_kg_") and k not in self.pii}
        if isinstance(value, (list, tuple)):
            return [self._plain(v) for v in value]
        return _scalar(value)

    def run(self, guarded):
        if not isinstance(guarded, GuardedCypher):
            raise AgentError("Only guarded Cypher can be executed")
        namespace = self.namespaces.get(guarded.graph)
        if namespace is None:
            raise QueryFailed(f"The {guarded.graph} graph is not available")
        failure = {}

        def work(tx):
            try:
                records = []
                for record in tx.run(guarded.cypher, namespace=namespace):
                    records.append(record)
                    if len(records) > self.max_rows:
                        break
                return records
            except Exception as exc:
                code = getattr(exc, "code", None)
                failure["message"] = f"{code}: {str(getattr(exc, 'message', '') or '')[:300]}" if code else "Cypher query failed"
                raise

        try:
            records = self.client.read(work)
        except Exception:
            raise QueryFailed(failure.get("message") or "Neo4j is unavailable") from None
        columns = list(records[0].keys()) if records else []
        rows = [[self._plain(record[c]) for c in columns] for record in records[:self.max_rows]]
        return {"columns": columns, "rows": rows, "truncated": len(records) > self.max_rows}
