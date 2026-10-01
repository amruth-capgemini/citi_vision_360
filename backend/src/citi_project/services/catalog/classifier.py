"""Deterministic classification: roles, ontology mappings, domains, joins and case collisions.

Runs before any model. A mapping made here is exact (class-qualified name, unique
multi-word property name, or identifier values that all match one ontology ID pattern,
whatever the column is called) and consistent with the column's values; it gets
confidence 1.0 and review_state "auto". Everything else is left for the enricher and review.
"""

from collections import Counter
from dataclasses import dataclass, field
import re

from .harvester import value_pattern
from .ontology import NAMESPACE_COLUMN, SCHEMA_DOMAINS, class_domain

_SENSITIVE_NAMES = {"emplid", "name", "employee_name", "worker_name", "worker_alias", "email", "phone",
                    "mobile", "ssn", "national_id", "date_of_birth", "dob", "home_address"}
_SENSITIVE_PARTS = re.compile(r"(email|phone|ssn|passport|birth)")
_PERIOD_TOKENS = {"year", "month", "quarter", "period"}
MAX_VALUE_SET = 20
MAX_CATEGORY_LENGTH = 40
_COMPATIBLE = {"identifier": {"identifier"}, "date": {"date"}, "measure": {"integer", "decimal", "percentage", "money_cents"},
               "dimension": {"string", "boolean", "year_month"}, "text": {"string", "text"}}


@dataclass
class FieldClassification:
    name: str
    semantic_role: str
    ontology_class: str | None = None
    ontology_property: str | None = None
    confidence: float | None = None
    review_state: str = "auto"
    name_collision: bool = False
    masked: bool = False
    description: str | None = None
    business_name: str | None = None
    value_pattern: str | None = None
    value_set: tuple[str, ...] | None = None
    basis: str = "none"  # classifier | llm | none

    @property
    def mapping(self):
        return f"{self.ontology_class}.{self.ontology_property}" if self.ontology_class else None


@dataclass
class DatasetClassification:
    dataset_id: str
    grain: str
    domains: dict  # domain_id -> basis (source_system | fields | llm)
    fields: dict  # column name -> FieldClassification
    summary: str | None = None
    review_state: str = "auto"


@dataclass
class Classification:
    datasets: dict = field(default_factory=dict)


def tokens(name):
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return [t for t in re.split(r"[^0-9A-Za-z]+", spaced.lower()) if t]


def is_sensitive(name):
    lowered = "_".join(tokens(name))
    return lowered in _SENSITIVE_NAMES or bool(_SENSITIVE_PARTS.search(lowered))


class Classifier:
    def __init__(self, registry):
        """``registry`` is the business ontology; mappings name its classes and properties."""
        self.registry = registry
        # Catalog metadata classes (module "data": Dataset, DataField ...) describe tables, not business facts.
        business = {cid: cls for cid, cls in registry.classes.items() if not cls.abstract and cls.module != "data"}
        self.prefixes = {}
        for cid, cls in business.items():
            for label in (cid, *cls.synonyms):
                self.prefixes.setdefault("_".join(tokens(label)), set()).add(cid)
        self.unqualified = {}
        for cid, cls in business.items():
            for prop in registry.properties_for_class(cid).values():
                for label in (prop.id, *prop.synonyms):
                    if len(tokens(label)) >= 2:
                        self.unqualified.setdefault(label, set()).add((cid, prop.id))

    # --------------------------------------------------------------- mapping

    def compatible(self, class_id, property_id, role, column):
        """A property fits a column when datatypes agree and every value satisfies its pattern or enum."""
        prop = self.registry.get_property(class_id, property_id)
        if prop.datatype not in _COMPATIBLE[role]:
            return False
        if column.distinct_truncated:
            return prop.enum is None and not (property_id == self.registry.get_class(class_id).key and self.registry.get_class(class_id).id_pattern)
        values = column.distinct_values
        cls = self.registry.get_class(class_id)
        if property_id == cls.key and cls.id_pattern and not all(re.fullmatch(cls.id_pattern, v) for v in values):
            return False
        if prop.pattern and not all(re.fullmatch(prop.pattern, v) for v in values):
            return False
        return prop.enum is None or set(values) <= set(prop.enum)

    def _name_match(self, name):
        parts = tokens(name)
        found = set()
        for i in range(len(parts) - 1, 0, -1):  # longest class prefix first
            for cid in self.prefixes.get("_".join(parts[:i]), ()):
                rests = ["_".join(parts[i:]), "_".join(parts[i - 1:])]
                if rests[0] == "id" and self.registry.get_class(cid).key:
                    found.add((cid, self.registry.get_class(cid).key))
                    continue
                for rest in rests:
                    prop = self.registry.property_for_field(cid, rest)
                    if prop is not None:
                        found.add((cid, prop.id))
                        break
            if found:
                return found
        return self.unqualified.get("_".join(parts), set())

    def _value_match(self, name, column):
        # Names are not required to look like IDs: Contract_Number or Invoice_PO qualify by their values.
        if not column.distinct_values or column.distinct_truncated:
            return set()
        classes = {tuple(c for c in self.registry.classify_identifier(v) if self.registry.get_class(c).module != "data")
                   for v in column.distinct_values}
        if len(classes) == 1 and len(next(iter(classes))) == 1:
            cid = next(iter(classes))[0]
            return {(cid, self.registry.get_class(cid).key)}
        return set()

    # ----------------------------------------------------------------- roles

    def role(self, column, mapped=None):
        name = column.name
        if mapped and self.registry.get_property(*mapped).datatype == "identifier":
            return "identifier"
        if name.upper().endswith("ID") or (column.distinct_values and not column.distinct_truncated
                                           and all(self.registry.classify_identifier(v) for v in column.distinct_values)):
            return "identifier"
        if column.dates:
            return "date"
        if column.numeric:
            return "dimension" if _PERIOD_TOKENS & set(tokens(name)) else "measure"
        if 0 < column.distinct_count <= MAX_VALUE_SET and column.max_length <= MAX_CATEGORY_LENGTH:
            return "dimension"
        return "text"

    def field(self, column, collision):
        masked = is_sensitive(column.name)
        mapping = None
        for match in (self._name_match(column.name), self._value_match(column.name, column)):
            if len(match) == 1:
                cid, pid = next(iter(match))
                if self.compatible(cid, pid, self.role(column, (cid, pid)), column):
                    mapping = (cid, pid)
                    break
        role = self.role(column, mapping)
        result = FieldClassification(column.name, role, name_collision=collision)
        if mapping:
            prop = self.registry.get_property(*mapping)
            masked = masked or prop.pii
            result.ontology_class, result.ontology_property = mapping
            result.description = " ".join(prop.description.split())
            result.business_name = concept_name(self.registry, *mapping)
            result.basis = "classifier"
            # Case-colliding names are never auto-mapped at full confidence.
            result.confidence, result.review_state = (0.5, "unreviewed") if collision else (1.0, "auto")
        result.masked = masked
        if not masked and role in ("identifier", "date", "dimension") and column.distinct_values and not column.distinct_truncated:
            result.value_pattern = value_pattern(column.distinct_values)
        if not masked and role == "dimension" and not column.distinct_truncated and 0 < len(column.distinct_values) <= MAX_VALUE_SET:
            result.value_set = tuple(column.distinct_values)
        return result

    # --------------------------------------------------------------- dataset

    def classify(self, harvest):
        result = Classification()
        for table in harvest.tables:
            folded = Counter(c.name.casefold() for c in table.columns)
            fields = {c.name: self.field(c, folded[c.name.casefold()] > 1) for c in table.columns}
            domains = {}
            if table.schema in SCHEMA_DOMAINS:
                domains[SCHEMA_DOMAINS[table.schema]] = "source_system"
            for f in fields.values():
                if f.ontology_class and f.semantic_role != "identifier" and f.review_state == "auto":
                    domains.setdefault(class_domain(self.registry, f.ontology_class), "fields")
            result.datasets[table.dataset_id] = DatasetClassification(table.dataset_id, grain(table), domains, fields)
        return result


def grain(table):
    key = [k for k in table.primary_key if k != NAMESPACE_COLUMN]
    if not key or all(k.startswith("_source_") for k in key):
        return "One row per source record; the table has no unique business key."
    scope = " within one graph namespace" if NAMESPACE_COLUMN in table.primary_key else ""
    return f"One row per {' + '.join(key)}{scope}."


def humanize(name):
    """Readable fallback name for a field no rule or model has named."""
    words = tokens(name)
    return " ".join(words).capitalize() if words else name


def concept_name(registry, class_id, property_id):
    """Business name of Class.property, for example Contract.end_date -> "Contract end date"."""
    cls = registry.get_class(class_id)
    base = tokens(class_id)
    known = set(base) | {w for s in cls.synonyms for w in tokens(s)}
    words = tokens(property_id)
    if property_id == cls.key and set(words) - {"id"} <= known:
        words = [*base, "id"]
    elif property_id != cls.key:
        words = [*base, *(w for w in words if w not in base)]
    text = " ".join("ID" if w == "id" else w for w in words)
    return text[0].upper() + text[1:]
