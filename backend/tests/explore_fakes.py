"""Offline doubles for the explorer: the real tables in SQLite, a scripted graph and scripted explorer steps.

Guarded SQL is transpiled from PostgreSQL to SQLite by sqlglot, so explorer tests run
real queries over the loaded CSV rows without a database server.
"""

from copy import deepcopy
from functools import lru_cache
import sqlite3
import threading

import sqlglot

from agent_fakes import ScriptedModel
from test_catalog import MemorySource
from citi_project.services.agents.executors import QueryFailed
from citi_project.services.agents.query_guard import SchemaIndex
from citi_project.services.catalog import Classifier, build_payload, harvest, load_catalog_registry
from citi_project.services.ontology import OntologyRegistry
from citi_project.services.postgres.tables import SYSTEMS


@lru_cache(maxsize=1)
def schema_index():
    business = OntologyRegistry.load()
    harvested = harvest(MemorySource(), list(SYSTEMS))
    return SchemaIndex.from_payload(build_payload(harvested, Classifier(business).classify(harvested), load_catalog_registry(), business))


class SqliteExecutor:
    """Runs guarded SQL over the CSV rows loaded into in-memory SQLite, one attached database per schema."""

    def __init__(self, max_rows=50):
        self.max_rows, self.queries, self._lock = max_rows, [], threading.Lock()
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        # SQLite column names are case-insensitive; a case-colliding column is stored under an alias.
        self.aliases = {}
        for schema in SYSTEMS:
            self.conn.execute(f"ATTACH DATABASE ':memory:' AS {schema}")
        for (schema, table), (_, fields, rows) in MemorySource().tables_.items():
            columns, seen = [], set()
            for c in [*fields, "_source_line", "_source_file"]:
                if c.casefold() in seen:
                    self.aliases[f'"{c}"'] = f'"{c}__case"'
                    c += "__case"
                seen.add(c.casefold())
                columns.append(c)
            self.conn.execute(f'CREATE TABLE {schema}.{table} ({", ".join(f"{chr(34)}{c}{chr(34)} TEXT" for c in columns)})')
            self.conn.executemany(f'INSERT INTO {schema}.{table} VALUES ({", ".join("?" for _ in columns)})',
                                  [[r[f] or None for f in fields] + [str(n), f"{table}.csv"] for n, r in enumerate(rows, 2)])

    def run(self, guarded):
        with self._lock:
            self.queries.append(guarded.sql)
            sql = sqlglot.transpile(guarded.sql, read="postgres", write="sqlite")[0]
            for name, alias in self.aliases.items():
                sql = sql.replace(name, alias)
            try:
                cursor = self.conn.execute(sql)
            except sqlite3.Error as exc:
                raise QueryFailed(f"PostgreSQL error: {exc}") from None
            columns = [d[0].removesuffix("__case") for d in cursor.description]
            rows = cursor.fetchmany(self.max_rows + 1)
        return {"columns": columns, "rows": [list(r) for r in rows[:self.max_rows]], "truncated": len(rows) > self.max_rows}


class FixedExecutor:
    """Returns fixed results in order (or raises), recording each guarded query."""

    def __init__(self, *results, max_rows=50):
        self.results, self.queries, self.max_rows = list(results), [], max_rows

    def run(self, guarded):
        self.queries.append(guarded)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return deepcopy(result)


def step(action, query=None, *, purpose="initial", graph=None, keep=(), concepts=(), terms=(), datasets=()):
    return {"thought": "scripted", "action": action, "purpose": purpose, "concepts": list(concepts), "terms": list(terms),
            "datasets": list(datasets), "graph": graph, "query": query, "keep": list(keep)}


class ExplorerModel(ScriptedModel):
    """ScriptedModel plus scripted explorer steps per specialist; a missing script finishes immediately."""

    def __init__(self, route_result, calls, steps=None, *, synthesis=None, fail_explore=None):
        super().__init__(route_result, calls, synthesis=synthesis)
        self.steps = {name: list(script) for name, script in (steps or {}).items()}
        self.fail_explore = fail_explore
        self._lock = threading.Lock()

    def complete(self, stage, prompt, payload, schema):
        if not stage.startswith("explore_"):
            return super().complete(stage, prompt, payload, schema)
        with self._lock:
            self.requests.append((stage, deepcopy(payload), deepcopy(schema)))
            if self.fail_explore is not None:
                raise self.fail_explore
            script = self.steps.get(stage.removeprefix("explore_"), [])
            return script.pop(0) if script else step("finish")
