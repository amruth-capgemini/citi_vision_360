"""Typed, immutable definitions loaded from the ontology YAML modules."""

from __future__ import annotations

from dataclasses import dataclass, field

DATATYPES = frozenset(
    {
        "string",
        "text",
        "identifier",
        "integer",
        "decimal",
        "boolean",
        "date",
        "year_month",
        "money_cents",
        "percentage",
    }
)
PATTERN_DATATYPES = frozenset({"string", "identifier"})
ENUM_DATATYPES = frozenset({"string"})
PROPERTY_CARDINALITIES = ("one", "many")
RELATION_CARDINALITIES = ("one_to_one", "one_to_many", "many_to_one", "many_to_many")
CLASS_KINDS = ("entity", "document", "clause", "fact")
DEFAULT_KIND = "entity"


def normalize_text(text: str) -> str:
    """Collapse whitespace so YAML folding/indentation never changes meaning or hash."""
    return " ".join(text.split())


@dataclass(frozen=True)
class PropertyDef:
    id: str
    datatype: str
    description: str
    required: bool = False
    cardinality: str = "one"
    enum: tuple[str, ...] | None = None
    pattern: str | None = None
    pii: bool = False
    synonyms: tuple[str, ...] = ()

    def canonical(self) -> dict:
        return {
            "id": self.id,
            "datatype": self.datatype,
            "description": normalize_text(self.description),
            "required": self.required,
            "cardinality": self.cardinality,
            "enum": sorted(self.enum) if self.enum is not None else None,
            "pattern": self.pattern,
            "pii": self.pii,
            "synonyms": sorted(self.synonyms),
        }


@dataclass(frozen=True)
class OntologyClass:
    id: str
    label: str
    definition: str
    module: str
    source_file: str
    parent: str | None = None
    kind: str | None = None  # resolved through inheritance by the registry
    abstract: bool = False
    key: str | None = None  # resolved through inheritance by the registry
    id_pattern: str | None = None
    fibo_uri: str | None = None
    synonyms: tuple[str, ...] = ()
    properties: tuple[PropertyDef, ...] = field(default=())

    def canonical(self) -> dict:
        # module and source_file are organisational only: moving a class between files
        # does not change its meaning, so they are excluded from the version hash.
        return {
            "id": self.id,
            "label": normalize_text(self.label),
            "definition": normalize_text(self.definition),
            "parent": self.parent,
            "kind": self.kind,
            "abstract": self.abstract,
            "key": self.key,
            "id_pattern": self.id_pattern,
            "fibo_uri": self.fibo_uri,
            "synonyms": sorted(self.synonyms),
            "properties": [p.canonical() for p in sorted(self.properties, key=lambda p: p.id)],
        }


@dataclass(frozen=True)
class RelationDef:
    id: str
    label: str
    source: tuple[str, ...]
    target: tuple[str, ...]
    cardinality: str
    description: str
    module: str
    source_file: str
    synonyms: tuple[str, ...] = ()
    disjoint_with: tuple[str, ...] = ()
    properties: tuple[PropertyDef, ...] = ()

    def canonical(self) -> dict:
        return {
            "id": self.id,
            "label": normalize_text(self.label),
            "source": sorted(self.source),
            "target": sorted(self.target),
            "cardinality": self.cardinality,
            "description": normalize_text(self.description),
            "synonyms": sorted(self.synonyms),
            "disjoint_with": sorted(self.disjoint_with),
            "properties": [p.canonical() for p in sorted(self.properties, key=lambda p: p.id)],
        }


@dataclass(frozen=True)
class OntologyIssue:
    """One precise load problem: where it is and which ID is at fault."""

    file: str
    message: str
    module: str | None = None
    offending_id: str | None = None

    def __str__(self) -> str:
        where = self.file if self.module is None else f"{self.file} (module {self.module})"
        subject = f" {self.offending_id}:" if self.offending_id else ""
        return f"{where}:{subject} {self.message}"


class OntologyError(Exception):
    """Base class for ontology failures."""


class OntologyLoadError(OntologyError):
    """The ontology files are structurally or semantically invalid."""

    def __init__(self, issues: list[OntologyIssue]):
        self.issues = issues
        super().__init__(
            f"{len(issues)} ontology issue(s):\n" + "\n".join(f"  - {i}" for i in issues)
        )


class OntologyValidationError(OntologyError, ValueError):
    """A class, property, value or relation does not conform to the loaded ontology."""


class UnknownClassError(OntologyValidationError):
    pass


class UnknownPropertyError(OntologyValidationError):
    pass


class UnknownRelationError(OntologyValidationError):
    pass


class InvalidValueError(OntologyValidationError):
    pass


class InvalidRelationEndpointError(OntologyValidationError):
    pass
