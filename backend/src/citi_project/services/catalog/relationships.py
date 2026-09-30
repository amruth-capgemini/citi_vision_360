"""Inferred foreign keys from values (inclusion dependencies), independent of column names.

For each group of identifier fields that carry the same thing (the same ontology concept,
or failing that the same value pattern), the group's *home key* is chosen among fields
that are unique in their dataset. Every other field in the group whose values are all
contained in the home key REFERENCES it. Evidence decides confidence:

- same classifier concept on both sides and 100% containment: confidence 1.0, auto
- a model-proposed concept on either side: 0.9, unreviewed
- same value pattern only: 0.8, unreviewed

Low-information fields (fewer than two distinct values, masked, truncated) never take part.
"""

from dataclasses import dataclass

from .ontology import NAMESPACE_COLUMN, class_domain

MIN_DISTINCT = 2
MIN_CONTAINMENT = 100.0


@dataclass(frozen=True)
class Reference:
    source: str  # child field_id
    target: str  # home key field_id
    containment_pct: float
    basis: str  # concept | pattern
    confidence: float
    review_state: str


@dataclass(frozen=True)
class _Candidate:
    field_id: str
    dataset_id: str
    schema: str
    column: object
    field: object

    @property
    def values(self):
        return set(self.column.distinct_values)


def _candidates(harvest, classification):
    for table in harvest.tables:
        dataset = classification.datasets[table.dataset_id]
        for column in table.columns:
            f = dataset.fields[column.name]
            if (f.semantic_role == "identifier" and not f.masked and not column.distinct_truncated
                    and len(column.distinct_values) >= MIN_DISTINCT):
                yield _Candidate(f"{table.dataset_id}.{column.name}", table.dataset_id, table.schema, column, f)


def _group(candidate):
    f = candidate.field
    return ("concept", f.mapping) if f.mapping else ("pattern", f.value_pattern) if f.value_pattern else None


def _home_score(candidate, classification, registry, tables):
    """Rank candidate keys; the lowest wins. In order:

    1. the column alone is the dataset's primary key (apart from the namespace column)
    2. the dataset is a reference table, not a fact table (it has no measure fields)
    3. the dataset is in the entity's own business domain
    4. the dataset describes more distinct properties of the entity
    5. the dataset is narrower (entity masters are narrow, fact tables wide)
    6. a stable order
    """
    f, dataset, table = candidate.field, classification.datasets[candidate.dataset_id], tables[candidate.dataset_id]
    sole_key = [k for k in table.primary_key if k != NAMESPACE_COLUMN] == [candidate.column.name]
    in_domain = bool(f.ontology_class) and class_domain(registry, f.ontology_class) in dataset.domains
    properties = {o.ontology_property for o in dataset.fields.values() if f.ontology_class and o.ontology_class == f.ontology_class}
    fact = any(o.semantic_role == "measure" for o in dataset.fields.values())
    return (not sole_key, fact, not in_domain, -len(properties), len(table.columns), candidate.dataset_id, candidate.column.name)


def discover_references(harvest, classification, registry):
    """``registry`` is the business ontology. Returns references sorted by child field."""
    tables = {t.dataset_id: t for t in harvest.tables}
    groups = {}
    for candidate in _candidates(harvest, classification):
        key = _group(candidate)
        if key:
            groups.setdefault(key, []).append(candidate)
    found = {}
    for (kind, _), members in sorted(groups.items(), key=lambda g: (g[0][0] != "concept", g[0][1])):
        keys = [m for m in members if m.column.unique]
        if not keys:
            continue
        home = min(keys, key=lambda m: _home_score(m, classification, registry, tables))
        for child in members:
            if child.field_id == home.field_id or child.field_id in found:
                continue
            containment = round(len(child.values & home.values) * 100 / len(child.values), 2)
            if containment < MIN_CONTAINMENT:
                continue
            if kind == "concept":
                auto = child.field.basis == home.field.basis == "classifier" and child.field.review_state == home.field.review_state == "auto"
                confidence, state = (1.0, "auto") if auto else (0.9, "unreviewed")
            else:
                confidence, state = 0.8, "unreviewed"
            found[child.field_id] = Reference(child.field_id, home.field_id, containment, kind, confidence, state)
    return [found[k] for k in sorted(found)]


def dataset_links(references):
    """Dataset-level summary: (child dataset, home dataset) -> sorted "child -> key" strings."""
    links = {}
    for ref in references:
        source, target = ref.source.rsplit(".", 1), ref.target.rsplit(".", 1)
        if source[0] != target[0]:
            links.setdefault((source[0], target[0]), set()).add(f"{source[1]} -> {target[1]}")
    return {pair: sorted(keys) for pair, keys in sorted(links.items())}
