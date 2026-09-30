"""Guards for model-authored read-only SQL and Cypher, checked in code before anything runs.

The model writes the query; this module decides whether it may run. SQL is parsed
with sqlglot and regenerated from the checked tree, so what runs is exactly what was
checked. Cypher has no parser here, so the rules are conservative: anything the
checker cannot recognize is refused, and the executor still uses a read transaction.
"""

from dataclasses import dataclass
import difflib
import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from .contracts import AgentError

MAX_QUERY_CHARS = 4000
LINEAGE_COLUMNS = ("_source_line", "_source_file")


class QueryRejected(AgentError):
    """A guard refusal. The message is written by this module and is safe to show the model."""


@dataclass(frozen=True)
class FieldInfo:
    name: str
    business_name: str | None = None
    concept: str | None = None
    role: str | None = None
    masked: bool = False
    references: str | None = None  # field_id of the key this field references
    reference_basis: str | None = None
    reference_review_state: str | None = None


@dataclass(frozen=True)
class DatasetInfo:
    dataset_id: str
    fields: tuple[FieldInfo, ...]
    grain: str | None = None
    row_count: int | None = None
    primary_key: tuple[str, ...] = ()
    description: str | None = None

    @property
    def masked(self):
        return any(f.masked for f in self.fields)

    def field(self, name):
        return next((f for f in self.fields if f.name == name), None)


class SchemaIndex:
    """What the explorer may query: catalog datasets, their fields and inferred foreign keys."""

    def __init__(self, datasets):
        self.datasets = {d.dataset_id: d for d in datasets}
        self._children = {}
        for d in self.datasets.values():
            for f in d.fields:
                if f.references:
                    self._children.setdefault(f.references, []).append((d.dataset_id, f))

    @classmethod
    def from_rows(cls, rows):
        """Rows as returned by ``CatalogQueryService.schema_index``."""
        datasets = []
        for row in rows:
            fields = tuple(FieldInfo(f["name"], f.get("business_name"), f.get("concept"), f.get("role"), bool(f.get("masked")),
                                     f.get("references"), f.get("basis"), f.get("review_state")) for f in row["fields"])
            datasets.append(DatasetInfo(row["dataset_id"], fields, row.get("grain"), row.get("row_count"),
                                        tuple(row.get("primary_key") or ()), row.get("description")))
        return cls(datasets)

    @classmethod
    def from_payload(cls, payload):
        """Build the same index from a catalog GraphPayload, without reading Neo4j."""
        entities = {(e.class_id, e.identity or e.properties.get(_KEYS.get(e.class_id, ""))): e for e in payload.entities}
        fields, refs, maps = {}, {}, {}
        for r in payload.relationships:
            if r.type == "HAS_FIELD":
                fields.setdefault(r.source.identity, []).append(r.target.identity)
            elif r.type == "REFERENCES":
                refs[r.source.identity] = (r.target.identity, r.properties.get("basis"), r.properties.get("review_state"))
            elif r.type == "MAPS_TO":
                maps[r.source.identity] = r.target.identity
        rows = []
        for (cls_id, identity), entity in sorted(entities.items(), key=lambda i: str(i[0])):
            if cls_id != "Dataset":
                continue
            p = entity.properties
            columns = sorted((entities[("DataField", fid)].properties for fid in fields.get(identity, [])), key=lambda f: f["position"])
            rows.append({"dataset_id": identity, "grain": p.get("grain"), "row_count": p.get("row_count"),
                         "primary_key": p.get("primary_key"), "description": p.get("description"),
                         "fields": [{"name": f["path"], "business_name": f.get("business_name"),
                                     "concept": maps.get(f["field_id"]), "role": f.get("semantic_role"), "masked": f.get("masked"),
                                     "references": refs.get(f["field_id"], (None,))[0], "basis": refs.get(f["field_id"], (None, None))[1],
                                     "review_state": refs.get(f["field_id"], (None, None, None))[2]} for f in columns]})
        return cls.from_rows(rows)

    @classmethod
    def from_catalog(cls, catalog_query):
        return cls.from_rows(catalog_query.schema_index())

    def links(self, dataset_id, column):
        """Every other field that joins to this one through the same key, with how it was inferred."""
        dataset = self.datasets.get(dataset_id)
        field = dataset.field(column) if dataset else None
        if field is None:
            return []
        home = field.references or (f"{dataset_id}.{column}" if f"{dataset_id}.{column}" in self._children else None)
        if home is None:
            return []
        out = []
        home_dataset, home_field = home.rsplit(".", 1)
        if home != f"{dataset_id}.{column}":
            out.append({"dataset": home_dataset, "field": home_field, "via": home, "basis": field.reference_basis,
                        "review_state": field.reference_review_state})
        for child_dataset, child in self._children.get(home, []):
            if (child_dataset, child.name) != (dataset_id, column):
                out.append({"dataset": child_dataset, "field": child.name, "via": home, "basis": child.reference_basis,
                            "review_state": child.reference_review_state})
        return out

    def concept(self, dataset_id, column):
        dataset = self.datasets.get(dataset_id)
        field = dataset.field(column) if dataset else None
        return field.concept if field else None

    @staticmethod
    def _describe(field):
        # The column name stands alone so the model copies it exactly; annotations are separate keys.
        entry = {"column": field.name}
        if field.business_name and field.business_name.casefold() != field.name.replace("_", " ").casefold():
            entry["meaning"] = field.business_name
        if field.concept:
            entry["concept"] = field.concept
        if field.masked:
            entry["masked"] = True
        return entry

    def search(self, concepts=(), terms=(), datasets=(), *, limit=10):
        """Compact catalog view for the model: matching datasets, their matching and key fields."""
        concepts = [c for c in concepts if isinstance(c, str)]
        terms = [t.casefold() for t in terms if isinstance(t, str) and t.strip()]
        out = []
        for d in self.datasets.values():
            listed = d.dataset_id in datasets
            def hit(f):
                return (any(f.concept == c or (f.concept or "").startswith(c + ".") for c in concepts)
                        or any(t in f.name.casefold() or t in (f.business_name or "").casefold() for t in terms))
            matched = [f for f in d.fields if hit(f)]
            if not (listed or matched or any(t in d.dataset_id for t in terms)):
                continue
            keys = [f for f in d.fields if f.role == "identifier" or f.references or f.name in d.primary_key]
            shown = d.fields if listed else list(dict.fromkeys(matched + keys))[:25]
            out.append(((not listed, -len(matched), len(d.fields), d.dataset_id),
                        {"dataset": d.dataset_id, "grain": d.grain, "rows": d.row_count, "field_count": len(d.fields),
                         "fields": [self._describe(f) for f in shown[:130]],
                         "masked_fields": [f.name for f in d.fields if f.masked]}))
        return [entry for _, entry in sorted(out, key=lambda e: e[0])[:limit]]

    def overview(self):
        return [{"dataset": d.dataset_id, "grain": d.grain, "rows": d.row_count, "fields": len(d.fields)}
                for d in sorted(self.datasets.values(), key=lambda d: d.dataset_id)]


_KEYS = {"Dataset": "dataset_id", "DataField": "field_id"}


@dataclass(frozen=True)
class GuardedSql:
    sql: str
    datasets: tuple[str, ...]
    columns: tuple[tuple[str, str], ...]  # (dataset_id, column) for every checked column reference


def _names(*names):
    return tuple(c for c in (getattr(exp, n, None) for n in names) if isinstance(c, type))


_SQL_FORBIDDEN = _names("Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable", "Command", "Copy",
                        "Set", "SetItem", "Into", "Lock", "TruncateTable", "Grant", "Revoke", "Use", "Transaction", "Commit",
                        "Rollback", "LoadData", "Pragma", "Describe", "Analyze", "Show", "Kill", "Comment", "Cache",
                        "Uncache", "Refresh", "Summarize")
# Class names from sqlglot's typed function nodes. Unknown or unlisted functions are refused.
ALLOWED_FUNCTIONS = frozenset({
    "Abs", "ArrayAgg", "Avg", "Case", "Cast", "TryCast", "Coalesce", "Concat", "ConcatWs", "Count", "Extract", "Greatest",
    "Least", "GroupConcat", "If", "Length", "Lower", "Upper", "Max", "Min", "Nullif", "Round", "SplitPart", "StrToDate",
    "Substring", "Sum", "Trim", "DateTrunc", "TimestampTrunc", "TimeToStr", "ToChar", "Floor", "Ceil", "StrPosition",
    "Left", "Right", "Replace", "Stddev", "StddevPop", "StddevSamp", "Variance", "Initcap", "Pad", "RowNumber", "Rank",
    "DenseRank", "Lag", "Lead", "FirstValue", "LastValue", "Ntile", "CountIf", "ToNumber", "Date", "DateDiff", "DateAdd",
    "DateSub", "RegexpLike", "RegexpILike", "StartsWith", "Exists", "AnyValue",
})
ALLOWED_ANONYMOUS = frozenset({"to_char", "to_number", "date_part", "string_agg", "initcap", "btrim", "lpad", "rpad",
                               "position", "split_part", "to_date", "age"})


def _star(projection):
    return isinstance(projection, exp.Star) or (isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star))


def _has_column(dataset, name):
    folded = name.casefold()
    return any(n.casefold() == folded for n in [*(f.name for f in dataset.fields), *LINEAGE_COLUMNS])


def _select_datasets(select, index, lookup):
    """Datasets read directly by one SELECT, or None when it reads a CTE or subquery."""
    sources = [s.this for s in [select.args.get("from") or select.args.get("from_"), *(select.args.get("joins") or [])] if s is not None]
    out = []
    for source in sources:
        dataset_id = lookup.get(f"{source.db}.{source.name}".casefold()) if isinstance(source, exp.Table) and source.db else None
        if dataset_id is None:
            return None
        out.append(index.datasets[dataset_id])
    return out or None


def _column_scope(column, name, index, lookup):
    """SQL scoping: the nearest enclosing SELECT that reads the column's table, then outer ones.

    UNION branches are siblings, not ancestors, so a column is never resolved against another
    branch's table. Returns None when a scope reads a CTE or subquery (the caller falls back).
    """
    node, nearest = column, None
    while (select := node.find_ancestor(exp.Select)) is not None:
        candidates = _select_datasets(select, index, lookup)
        if candidates is None:
            return None
        nearest = nearest or candidates
        if any(_has_column(d, name) for d in candidates):
            return candidates
        node = select
    return nearest


def guard_sql(query, index, *, max_rows=50):
    """Check a model-written query against the catalog and return the SQL that may run."""
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
        raise QueryRejected(f"SQL must be a nonempty string of at most {MAX_QUERY_CHARS} characters")
    try:
        statements = [s for s in sqlglot.parse(query, read="postgres") if s is not None]
    except SqlglotError:
        raise QueryRejected("SQL could not be parsed as PostgreSQL") from None
    if len(statements) != 1:
        raise QueryRejected("Exactly one SQL statement is allowed")
    root = statements[0]
    if not isinstance(root, exp.Query):
        raise QueryRejected("Only SELECT queries are allowed")
    for node in root.walk():
        if isinstance(node, _SQL_FORBIDDEN):
            raise QueryRejected(f"{type(node).__name__} is not allowed; queries are read-only")
        # sqlglot models AND/OR as Func subclasses (Connector); they are operators, not functions.
        if isinstance(node, exp.Func) and not isinstance(node, (exp.Anonymous, exp.Connector)) and type(node).__name__ not in ALLOWED_FUNCTIONS:
            raise QueryRejected(f"Function {type(node).__name__.upper()} is not allowed")
        if isinstance(node, exp.Anonymous) and node.name.casefold() not in ALLOWED_ANONYMOUS:
            raise QueryRejected(f"Function {node.name} is not allowed")

    ctes = {cte.alias_or_name.casefold() for cte in root.find_all(exp.CTE)}
    aliases = {a.alias for a in root.find_all(exp.Alias)} | {c.name for t in root.find_all(exp.TableAlias) for c in t.columns}
    lookup = {d.casefold(): d for d in index.datasets}
    referenced, qualifiers = [], {}
    for table in root.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise QueryRejected("Table functions are not allowed")
        if not table.db and table.name.casefold() in ctes:
            continue
        if table.catalog:
            raise QueryRejected("Database-qualified table names are not allowed")
        dataset_id = lookup.get(f"{table.db}.{table.name}".casefold())
        if dataset_id is None:
            raise QueryRejected(f"Unknown dataset {table.db + '.' if table.db else ''}{table.name}; "
                                "use schema.table names from search_catalog")
        schema, name = dataset_id.split(".")
        table.set("db", exp.to_identifier(schema, quoted=True))
        table.set("this", exp.to_identifier(name, quoted=True))
        referenced.append(dataset_id)
        qualifiers[table.alias_or_name.casefold()] = dataset_id
        qualifiers[name.casefold()] = dataset_id
    if not referenced:
        raise QueryRejected("The query must read at least one catalog dataset")
    datasets = [index.datasets[d] for d in dict.fromkeys(referenced)]

    if any(d.masked for d in datasets) and any(_star(p) for s in root.find_all(exp.Select) for p in s.expressions):
        raise QueryRejected("SELECT * is not allowed on datasets with masked fields; list the columns")

    checked = []
    for column in root.find_all(exp.Column):
        if isinstance(column.this, exp.Star):
            continue
        name = column.name
        if column.table and column.table.casefold() in qualifiers:
            scope = [index.datasets[qualifiers[column.table.casefold()]]]
        elif column.table:
            scope = datasets  # qualified by a CTE or subquery alias
        else:
            scope = _column_scope(column, name, index, lookup) or datasets
        spellings = {}
        for d in scope:
            names = [f.name for f in d.fields] + list(LINEAGE_COLUMNS)
            exact = [n for n in names if n == name]
            for n in exact or [n for n in names if n.casefold() == name.casefold()]:
                spellings.setdefault(n, []).append(d)
        if not spellings:
            if name in aliases or (column.table and column.table.casefold() not in qualifiers):
                continue
            known = sorted({f.name for d in scope for f in d.fields})
            close = difflib.get_close_matches(name, known, n=4, cutoff=0.5)
            raise QueryRejected(f"Unknown column {name} in {', '.join(d.dataset_id for d in scope)}"
                                + (f"; similar columns: {', '.join(close)}" if close else ""))
        if len(spellings) > 1 and name not in spellings:
            raise QueryRejected(f"Column {name} is ambiguous ({', '.join(sorted(spellings))}); quote the exact name")
        spelling = name if name in spellings else next(iter(spellings))
        for d in spellings[spelling]:
            field = d.field(spelling)
            if field is not None and field.masked:
                raise QueryRejected(f"Column {spelling} in {d.dataset_id} is masked")
            checked.append((d.dataset_id, spelling))
        column.set("this", exp.to_identifier(spelling, quoted=True))

    limit = max_rows + 1
    if isinstance(root, exp.Select):
        current = root.args.get("limit")
        value = current.expression if current is not None else None
        if not (isinstance(value, exp.Literal) and value.is_int and int(value.this) <= limit):
            root = root.limit(limit)
        sql = root.sql(dialect="postgres", comments=False)
    else:
        sql = f"SELECT * FROM ({root.sql(dialect='postgres', comments=False)}) AS guarded_query LIMIT {limit}"
    return GuardedSql(sql, tuple(d.dataset_id for d in datasets), tuple(dict.fromkeys(checked)))


@dataclass(frozen=True)
class GuardedCypher:
    cypher: str
    graph: str
    labels: tuple[str, ...]


_CYPHER_FORBIDDEN = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|LOAD|FOREACH|CALL|USE|SHOW|GRANT|DENY|REVOKE|TERMINATE|ALTER|"
    r"START|STOP|ENABLE|DISABLE|RENAME|FINISH|INSERT|IMPORT)\b", re.I)
_CYPHER_PROCEDURES = re.compile(r"\b(apoc|gds|dbms|db|genai|tx)\s*\.", re.I)
_NODE = (r"\(\s*(?P<var>[A-Za-z_]\w*)?\s*(?P<labels>(?::\s*`?[A-Za-z_]\w*`?\s*)*)"
         r"(?P<props>\{[^{}]*\})?\s*\)")
_REL = r"<?-\s*(?:\[[^\[\]]*\])?\s*->?"
_NODE_RE = re.compile(r"(?<![\w`])" + _NODE)
_NODE_NAMED = _NODE.replace("?P<var>", "?:").replace("?P<labels>", "?:").replace("?P<props>", "?:")
_CHAIN_RE = re.compile(r"(?<![\w`])" + _NODE_NAMED + r"(?:\s*" + _REL + r"\s*" + _NODE_NAMED + r")+")
_PART_RE = re.compile(r"\s*(?:[A-Za-z_]\w*\s*=\s*)?" + _NODE_NAMED + r"(?:\s*" + _REL + r"\s*" + _NODE_NAMED + r")*\s*")
_CLAUSE = re.compile(r"\b(OPTIONAL\s+MATCH|MATCH|WHERE|WITH|RETURN|UNWIND|ORDER\s+BY|SKIP|LIMIT|UNION(?:\s+ALL)?)\b", re.I)
_SCOPED = re.compile(r"_kg_namespace\s*:\s*\$namespace\b")
_ALIAS = re.compile(r"\b([A-Za-z_]\w*)\s+AS\s+([A-Za-z_]\w*)", re.I)


def _strip_cypher(query):
    """Remove comments; return (clean text to run, text with string contents masked for checking)."""
    clean, masked, i, n = [], [], 0, len(query)
    while i < n:
        ch = query[i]
        if query.startswith("//", i):
            while i < n and query[i] != "\n":
                i += 1
            continue
        if query.startswith("/*", i):
            end = query.find("*/", i + 2)
            if end < 0:
                raise QueryRejected("Unterminated Cypher comment")
            i = end + 2
            clean.append(" ")
            masked.append(" ")
            continue
        if ch in "'\"`":
            j = i + 1
            while j < n and query[j] != ch:
                j += 2 if query[j] == "\\" else 1
            if j >= n:
                raise QueryRejected("Unterminated Cypher string or identifier")
            clean.append(query[i:j + 1])
            masked.append(query[i:j + 1] if ch == "`" else ch + "_" * (j - i - 1) + ch)
            i = j + 1
            continue
        clean.append(ch)
        masked.append(ch)
        i += 1
    return "".join(clean), "".join(masked)


def _split_top(text):
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append((start, text[start:i]))
            start = i + 1
    parts.append((start, text[start:]))
    return parts


def guard_cypher(query, *, graph, pii_properties=()):
    """Check model-written read-only Cypher; the host binds $namespace for the chosen graph."""
    if graph not in ("business", "catalog"):
        raise QueryRejected("graph must be business or catalog")
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
        raise QueryRejected(f"Cypher must be a nonempty string of at most {MAX_QUERY_CHARS} characters")
    clean, masked = _strip_cypher(query)
    if ";" in masked.strip().rstrip(";"):
        raise QueryRejected("Exactly one Cypher statement is allowed")
    clean, masked = clean.strip().rstrip(";"), masked.strip().rstrip(";")
    found = _CYPHER_FORBIDDEN.search(masked) or _CYPHER_PROCEDURES.search(masked)
    if found:
        raise QueryRejected(f"{found.group(0).strip()} is not allowed; queries are read-only and may not call procedures")
    if not re.search(r"\bRETURN\b", masked, re.I):
        raise QueryRejected("The query must RETURN results")
    if masked.count("_kg_namespace") != len(_SCOPED.findall(masked)):
        raise QueryRejected("_kg_namespace may only be matched as {_kg_namespace: $namespace}")
    params = set(re.findall(r"\$(\w+)", masked))
    if params != {"namespace"}:
        raise QueryRejected("Use $namespace to scope every pattern; write other values as literals")
    for prop in pii_properties:
        if re.search(r"\.\s*`?" + re.escape(prop) + r"\b", masked):
            raise QueryRejected(f"Property {prop} is personal data and cannot be queried")
    if re.search(r"\b(properties|keys)\s*\(|\{\s*\.\*", masked, re.I):
        raise QueryRejected("Return named properties or whole nodes, not properties(), keys() or .*")

    # Pattern chains in position order: each must touch an anchored node or variable.
    events = []
    clauses = list(_CLAUSE.finditer(masked))
    match_spans = []
    for k, m in enumerate(clauses):
        if m.group(1).upper().endswith("MATCH"):
            end = clauses[k + 1].start() if k + 1 < len(clauses) else len(masked)
            depth = 0
            for i in range(m.end(), end):  # a subquery's closing brace ends its MATCH
                depth += masked[i] in "([{"
                depth -= masked[i] in ")]}"
                if depth < 0:
                    end = i
                    break
            match_spans.append((m.end(), end))
    in_match = lambda pos: any(a <= pos < b for a, b in match_spans)
    for start, end in match_spans:
        for offset, part in _split_top(masked[start:end]):
            if not part.strip():
                raise QueryRejected("Empty MATCH pattern")
            if not _PART_RE.fullmatch(part):
                raise QueryRejected("Unsupported MATCH pattern; use (var:Label {_kg_namespace: $namespace})-[:TYPE]->(var) chains")
            events.append((start + offset, "chain", list(_NODE_RE.finditer(part))))
    for chain in _CHAIN_RE.finditer(masked):
        if not in_match(chain.start()):
            events.append((chain.start(), "chain", list(_NODE_RE.finditer(chain.group(0)))))
    for node in _NODE_RE.finditer(masked):
        if not in_match(node.start()) and (node.group("labels") or node.group("props")) and not any(
                c.start() <= node.start() < c.end() for c in _CHAIN_RE.finditer(masked)):
            events.append((node.start(), "chain", [node]))
    if re.search(r"(?<![\w`])\(\s*[A-Za-z_]?\w*\s*:\s*[!%&|]", masked):
        raise QueryRejected("Label expressions are not supported; use a single label per node")
    for alias in _ALIAS.finditer(masked):
        events.append((alias.start(), "alias", alias))
    anchored, labels = set(), set()
    for _, kind, value in sorted(events, key=lambda e: (e[0], e[1])):
        if kind == "alias":
            if value.group(1) in anchored:
                anchored.add(value.group(2))
            continue
        variables = {n.group("var") for n in value if n.group("var")}
        if not (any(n.group("props") and _SCOPED.search(n.group("props")) for n in value) or variables & anchored):
            raise QueryRejected("Every pattern must include a node with {_kg_namespace: $namespace} or a variable bound to one")
        anchored |= variables
        labels |= {label.strip(" `") for n in value for label in n.group("labels").split(":") if label.strip(" `")}
    if not match_spans:
        raise QueryRejected("The query must MATCH a namespace-anchored pattern")
    return GuardedCypher(clean, graph, tuple(sorted(labels)))
