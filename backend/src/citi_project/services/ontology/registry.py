"""Load, validate and query the governed ontology defined in ``ontology/*.yaml``.

Loading runs in two stages and reports every problem it finds, not just the first:

1. Structural: each module file is validated against ``ontology/_schema.yaml``.
2. Semantic: duplicate IDs across modules, unknown or cyclic parents, kind consistency,
   inherited-property overrides, keys, enums/patterns, relation endpoints, synonyms.

``ontology_version`` is a SHA-256 over the canonical content of every class and relation,
sorted by stable ID with whitespace-normalised text. YAML formatting, key order, list order
of synonyms/enums/endpoints and which file a definition lives in do not affect it. Any change
to an ID, label, definition/description, datatype, requirement, cardinality, enum, pattern,
key, parent, kind, synonym or endpoint produces a new version.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from .models import (
    CLASS_KINDS,
    DATATYPES,
    DEFAULT_KIND,
    ENUM_DATATYPES,
    PATTERN_DATATYPES,
    InvalidRelationEndpointError,
    InvalidValueError,
    OntologyClass,
    OntologyIssue,
    OntologyLoadError,
    PropertyDef,
    RelationDef,
    UnknownClassError,
    UnknownPropertyError,
    UnknownRelationError,
    normalize_text,
)

SCHEMA_FILE = "_schema.yaml"
ONTOLOGY_DIR_ENV = "CITI_ONTOLOGY_DIR"
CANONICAL_FORMAT = 1  # bump if canonicalisation itself changes
_UNSET = object()
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_YEAR_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def validate_property_value(prop: PropertyDef, value: Any) -> None:
    """Validate a standalone property definition (including relationship properties)."""
    error = _check_value(prop, value)
    if error:
        raise InvalidValueError(f"{prop.id}: {error}")


def default_ontology_dir() -> Path:
    env = os.environ.get(ONTOLOGY_DIR_ENV)
    if env:
        return Path(env)
    # src/citi_project/services/ontology/registry.py -> repository root
    return Path(__file__).resolve().parents[4] / "ontology"


class OntologyRegistry:
    """Read-only view over a validated ontology."""

    def __init__(
        self,
        classes: Mapping[str, OntologyClass],
        relations: Mapping[str, RelationDef],
        source_dir: Path,
    ):
        self._classes = dict(classes)
        self._relations = dict(relations)
        self.source_dir = source_dir
        self._class_synonyms = _synonym_index(self._classes.values())
        self._relation_synonyms = _synonym_index(self._relations.values())
        self.ontology_version = _compute_version(self._classes, self._relations)

    # ------------------------------------------------------------------ loading

    @classmethod
    def load(cls, directory: str | Path | None = None, *, schema_path: str | Path | None = None) -> OntologyRegistry:
        """Load every module in ``directory``; ``schema_path`` defaults to its own ``_schema.yaml``."""
        directory = Path(directory) if directory is not None else default_ontology_dir()
        schema_path = Path(schema_path) if schema_path is not None else directory / SCHEMA_FILE
        if not directory.is_dir():
            raise OntologyLoadError([OntologyIssue(str(directory), "ontology directory not found")])
        if not schema_path.is_file():
            raise OntologyLoadError([OntologyIssue(str(schema_path), "module schema not found")])

        validator = Draft202012Validator(yaml.safe_load(schema_path.read_text(encoding="utf-8")))
        documents = _read_modules(directory, validator)
        classes, relations = _build(documents)
        return cls(classes, relations, directory)

    # ------------------------------------------------------------------ classes

    @property
    def classes(self) -> Mapping[str, OntologyClass]:
        return dict(self._classes)

    @property
    def relations(self) -> Mapping[str, RelationDef]:
        return dict(self._relations)

    def get_class(self, class_id: str) -> OntologyClass:
        try:
            return self._classes[class_id]
        except KeyError:
            hint = self._class_synonyms.get(class_id.casefold())
            suffix = f" (did you mean {hint!r}?)" if hint else ""
            raise UnknownClassError(f"unknown class {class_id!r}{suffix}") from None

    def resolve_class(self, name: str) -> OntologyClass:
        """Find a class by exact ID or by case-insensitive synonym."""
        if name in self._classes:
            return self._classes[name]
        target = self._class_synonyms.get(name.casefold())
        if target is None:
            raise UnknownClassError(f"no class or class synonym named {name!r}")
        return self._classes[target]

    def ancestors(self, class_id: str) -> tuple[str, ...]:
        """Parent chain from nearest parent to root."""
        self.get_class(class_id)
        return _ancestors(self._classes, class_id)

    def is_subclass_of(self, class_id: str, ancestor_id: str) -> bool:
        """Reflexive subclass test."""
        return class_id == ancestor_id or ancestor_id in self.ancestors(class_id)

    def subclasses(self, class_id: str, include_self: bool = True) -> list[str]:
        self.get_class(class_id)
        return sorted(
            c for c in self._classes if self.is_subclass_of(c, class_id) and (include_self or c != class_id)
        )

    def properties_for_class(
        self, class_id: str, include_inherited: bool = True
    ) -> dict[str, PropertyDef]:
        """Properties keyed by ID, root ancestor's first."""
        self.get_class(class_id)
        return _properties(self._classes, class_id, include_inherited)

    def get_property(self, class_id: str, property_id: str) -> PropertyDef:
        props = self.properties_for_class(class_id)
        if property_id not in props:
            raise UnknownPropertyError(f"class {class_id!r} has no property {property_id!r}")
        return props[property_id]

    def property_for_field(self, class_id: str, field_name: str) -> PropertyDef | None:
        """Map a source field name to a property by ID first, then by synonym."""
        props = self.properties_for_class(class_id)
        if field_name in props:
            return props[field_name]
        for prop in props.values():
            if field_name in prop.synonyms:
                return prop
        return None

    def document_classes(self, include_abstract: bool = False) -> list[str]:
        return self._classes_of_kind("document", include_abstract)

    def clause_classes(self, include_abstract: bool = False) -> list[str]:
        return self._classes_of_kind("clause", include_abstract)

    def classify_identifier(self, value: str) -> list[str]:
        """Classes whose ``id_pattern`` fully matches a raw identifier."""
        return sorted(
            c.id for c in self._classes.values() if c.id_pattern and re.fullmatch(c.id_pattern, value)
        )

    def _classes_of_kind(self, kind: str, include_abstract: bool) -> list[str]:
        return sorted(
            c.id
            for c in self._classes.values()
            if c.kind == kind and (include_abstract or not c.abstract)
        )

    # --------------------------------------------------------------- validation

    def validate_class(self, class_id: str, *, allow_abstract: bool = True) -> OntologyClass:
        cls_def = self.get_class(class_id)
        if cls_def.abstract and not allow_abstract:
            raise UnknownClassError(f"class {class_id!r} is abstract and cannot have instances")
        return cls_def

    def validate_property(self, class_id: str, property_id: str, value: Any = _UNSET) -> PropertyDef:
        """Check that the property exists on the class and, if given, that the value conforms."""
        prop = self.get_property(class_id, property_id)
        if value is not _UNSET:
            error = _check_value(prop, value)
            if error:
                raise InvalidValueError(f"{class_id}.{property_id}: {error}")
        return prop

    def validate_record(
        self, class_id: str, record: Mapping[str, Any], *, allow_unmapped: bool = False
    ) -> list[str]:
        """Validate a flat source record; field names may be property IDs or synonyms."""
        self.validate_class(class_id, allow_abstract=False)
        errors: list[str] = []
        seen: set[str] = set()
        for field_name, value in record.items():
            prop = self.property_for_field(class_id, field_name)
            if prop is None:
                if not allow_unmapped:
                    errors.append(f"{class_id}: field {field_name!r} maps to no property")
                continue
            seen.add(prop.id)
            error = _check_value(prop, value)
            if error:
                errors.append(f"{class_id}.{prop.id} (field {field_name!r}): {error}")
        for prop in self.properties_for_class(class_id).values():
            if prop.required and prop.id not in seen:
                errors.append(f"{class_id}.{prop.id}: required property missing")
        return errors

    def get_relation(self, relation_id: str) -> RelationDef:
        try:
            return self._relations[relation_id]
        except KeyError:
            hint = self._relation_synonyms.get(relation_id.casefold())
            suffix = f" (source synonym of {hint!r})" if hint else ""
            raise UnknownRelationError(f"unknown relation {relation_id!r}{suffix}") from None

    def resolve_relation(self, name: str) -> RelationDef:
        """Find a relation by exact ID or by case-insensitive synonym (source vocabulary)."""
        if name in self._relations:
            return self._relations[name]
        target = self._relation_synonyms.get(name.casefold())
        if target is None:
            raise UnknownRelationError(f"no relation or relation synonym named {name!r}")
        return self._relations[target]

    def validate_relation(self, relation_id: str, source_class: str, target_class: str) -> RelationDef:
        rel = self.get_relation(relation_id)
        self.get_class(source_class)
        self.get_class(target_class)
        if not any(self.is_subclass_of(source_class, s) for s in rel.source):
            raise InvalidRelationEndpointError(
                f"{relation_id}: source {source_class!r} is not one of {list(rel.source)}"
            )
        if not any(self.is_subclass_of(target_class, t) for t in rel.target):
            raise InvalidRelationEndpointError(
                f"{relation_id}: target {target_class!r} is not one of {list(rel.target)}"
            )
        return rel

    def allowed_relations(
        self, source_class: str | None = None, target_class: str | None = None
    ) -> list[RelationDef]:
        """Relations whose declared endpoints admit the given classes (subclass-aware)."""
        for cid in (source_class, target_class):
            if cid is not None:
                self.get_class(cid)
        return [
            rel
            for rel in sorted(self._relations.values(), key=lambda r: r.id)
            if (source_class is None or any(self.is_subclass_of(source_class, s) for s in rel.source))
            and (target_class is None or any(self.is_subclass_of(target_class, t) for t in rel.target))
        ]

    # ------------------------------------------------------------- extraction

    def json_schema_for_extraction(self, class_id: str) -> dict:
        """JSON Schema for LLM extraction output of one concrete class.

        Every ontology property must be present as a key; its value is null (not found, no
        evidence required) or ``{value, confidence, evidence{chunk_id, page, quote}}``.
        Cardinality-many properties hold a non-empty list of such objects. Ontology-required
        properties are still nullable here: absence is surfaced for review rather than
        forcing the model to fabricate evidence. Unknown keys are rejected.
        """
        cls_def = self.validate_class(class_id, allow_abstract=False)
        props = self.properties_for_class(class_id)
        fields = {}
        for prop in props.values():
            present = {
                "type": "object",
                "additionalProperties": False,
                "required": ["value", "confidence", "evidence"],
                "properties": {
                    "value": _value_schema(prop),
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence": {"$ref": "#/$defs/evidence"},
                },
            }
            if prop.cardinality == "many":
                present = {"type": "array", "minItems": 1, "items": present}
            fields[prop.id] = {
                "description": normalize_text(prop.description),
                "anyOf": [{"type": "null"}, present],
            }
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"urn:citi-project:extraction:{class_id}:{self.ontology_version}",
            "title": f"{cls_def.label} extraction",
            "description": normalize_text(cls_def.definition),
            "type": "object",
            "additionalProperties": False,
            "required": list(props),
            "properties": fields,
            "$defs": {
                "evidence": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["chunk_id", "page", "quote"],
                    "properties": {
                        "chunk_id": {"type": "string", "minLength": 1},
                        "page": {"type": ["integer", "null"], "minimum": 1},
                        "quote": {"type": "string", "minLength": 1},
                    },
                }
            },
            "x-ontology-version": self.ontology_version,
            "x-class-id": class_id,
        }

    def validate_extraction(self, class_id: str, payload: Any) -> list[str]:
        """Re-validate model output against the schema and the ontology value rules."""
        validator = Draft202012Validator(self.json_schema_for_extraction(class_id))
        errors = []
        for err in sorted(validator.iter_errors(payload), key=lambda e: list(map(str, e.absolute_path))):
            # "not valid under any of the given schemas" hides the cause; report the closest branch
            branches = [
                e for e in err.context if not (e.validator == "type" and e.validator_value == "null")
            ]
            cause = best_match(branches) if branches else err
            path = "/".join(map(str, cause.absolute_path)) or "<root>"
            errors.append(f"{path}: {cause.message}")
        if errors:
            return errors
        for prop_id, field_value in payload.items():
            if field_value is None:
                continue
            prop = self.get_property(class_id, prop_id)
            items = field_value if prop.cardinality == "many" else [field_value]
            for item in items:
                error = _check_scalar(prop, item["value"])
                if error:
                    errors.append(f"{prop_id}: {error}")
        return errors


# ---------------------------------------------------------------------- helpers


def _ancestors(classes: Mapping[str, OntologyClass], class_id: str) -> tuple[str, ...]:
    chain = []
    parent = classes[class_id].parent
    while parent is not None:
        chain.append(parent)
        parent = classes[parent].parent
    return tuple(chain)


def _properties(
    classes: Mapping[str, OntologyClass], class_id: str, include_inherited: bool = True
) -> dict[str, PropertyDef]:
    lineage = [*reversed(_ancestors(classes, class_id)), class_id] if include_inherited else [class_id]
    return {prop.id: prop for cid in lineage for prop in classes[cid].properties}


def _synonym_index(defs: Iterable[OntologyClass | RelationDef]) -> dict[str, str]:
    return {syn.casefold(): d.id for d in defs for syn in d.synonyms}


def _compute_version(classes: Mapping[str, OntologyClass], relations: Mapping[str, RelationDef]) -> str:
    canonical = {
        "format": CANONICAL_FORMAT,
        "classes": [classes[k].canonical() for k in sorted(classes)],
        "relations": [relations[k].canonical() for k in sorted(relations)],
    }
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _value_schema(prop: PropertyDef) -> dict:
    schema: dict[str, Any] = {
        "string": {"type": "string"},
        "text": {"type": "string", "minLength": 1},
        "identifier": {"type": "string", "minLength": 1},
        "integer": {"type": "integer"},
        "money_cents": {"type": "integer"},
        "decimal": {"type": "number"},
        "percentage": {"type": "number", "minimum": 0, "maximum": 100},
        "boolean": {"type": "boolean"},
        "date": {"type": "string", "format": "date", "pattern": _DATE_RE.pattern},
        "year_month": {"type": "string", "pattern": _YEAR_MONTH_RE.pattern},
    }[prop.datatype].copy()
    if prop.pattern:
        schema["pattern"] = prop.pattern
    if prop.enum is not None:
        schema["enum"] = list(prop.enum)
    return schema


def _check_value(prop: PropertyDef, value: Any) -> str | None:
    if value is None:
        return "required value is null" if prop.required else None
    if prop.cardinality == "many":
        if not isinstance(value, list):
            return f"cardinality 'many' expects a list, got {type(value).__name__}"
        for item in value:
            error = _check_scalar(prop, item)
            if error:
                return error
        return None
    if isinstance(value, list):
        return "cardinality 'one' does not accept a list"
    return _check_scalar(prop, value)


def _check_scalar(prop: PropertyDef, value: Any) -> str | None:
    dt = prop.datatype
    if dt in ("string", "text", "identifier"):
        if not isinstance(value, str):
            return f"expected {dt}, got {type(value).__name__}"
        if dt != "string" and not value:
            return f"{dt} must not be empty"
    elif dt in ("integer", "money_cents"):
        if isinstance(value, bool) or not isinstance(value, int):
            return f"expected {dt} (whole number), got {value!r}"
    elif dt in ("decimal", "percentage"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"expected {dt}, got {value!r}"
        if dt == "percentage" and not 0 <= value <= 100:
            return f"percentage {value!r} outside 0-100"
    elif dt == "boolean":
        if not isinstance(value, bool):
            return f"expected boolean, got {value!r}"
    elif dt == "date":
        if not isinstance(value, str) or not _DATE_RE.match(value):
            return f"expected ISO date YYYY-MM-DD, got {value!r}"
        try:
            date.fromisoformat(value)
        except ValueError:
            return f"invalid calendar date {value!r}"
    elif dt == "year_month":
        if not isinstance(value, str) or not _YEAR_MONTH_RE.match(value):
            return f"expected YYYY-MM, got {value!r}"
    if prop.pattern and not re.fullmatch(prop.pattern, value):
        return f"{value!r} does not match pattern {prop.pattern!r}"
    if prop.enum is not None and value not in prop.enum:
        return f"{value!r} not in allowed values {list(prop.enum)}"
    return None


# ------------------------------------------------------------------ YAML stage


def _read_modules(directory: Path, validator: Draft202012Validator) -> list[tuple[str, dict]]:
    issues: list[OntologyIssue] = []
    documents: list[tuple[str, dict]] = []
    modules_seen: dict[str, str] = {}
    paths = sorted(p for p in directory.glob("*.yaml") if not p.name.startswith("_"))
    if not paths:
        raise OntologyLoadError([OntologyIssue(str(directory), "no ontology module files found")])
    for path in paths:
        name = path.name
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            issues.append(OntologyIssue(name, f"invalid YAML: {exc}"))
            continue
        errors = list(validator.iter_errors(doc))
        if errors:
            module = doc.get("module") if isinstance(doc, dict) else None
            for err in sorted(errors, key=lambda e: list(map(str, e.absolute_path))):
                location, offending = _locate(doc, list(err.absolute_path))
                issues.append(OntologyIssue(name, f"{location}: {err.message}", module, offending))
            continue
        module = doc["module"]
        if module in modules_seen:
            issues.append(
                OntologyIssue(name, f"module name also declared in {modules_seen[module]}", module)
            )
            continue
        modules_seen[module] = name
        documents.append((name, doc))
    if issues:
        raise OntologyLoadError(issues)
    return documents


def _locate(doc: Any, path: list) -> tuple[str, str | None]:
    """Human-readable path plus the dotted IDs (e.g. ``Vendor.status``) along it."""
    node, parts, ids = doc, [], []
    for step in path:
        parts.append(f"[{step}]" if isinstance(step, int) else f".{step}")
        try:
            node = node[step]
        except (KeyError, IndexError, TypeError):
            break
        if isinstance(node, dict) and isinstance(node.get("id"), str):
            ids.append(node["id"])
    location = "".join(parts).lstrip(".") or "<root>"
    return location, ".".join(ids) or None


# -------------------------------------------------------------- semantic stage


def _parse_property(raw: dict) -> PropertyDef:
    return PropertyDef(
        id=raw["id"],
        datatype=raw["datatype"],
        description=raw["description"],
        required=raw.get("required", False),
        cardinality=raw.get("cardinality", "one"),
        enum=tuple(raw["enum"]) if "enum" in raw else None,
        pattern=raw.get("pattern"),
        pii=raw.get("pii", False),
        synonyms=tuple(raw.get("synonyms", ())),
    )


def _as_tuple(value: str | list[str]) -> tuple[str, ...]:
    return (value,) if isinstance(value, str) else tuple(value)


def _check_properties(
    props: Iterable[PropertyDef], owner: str, file: str, module: str, issues: list[OntologyIssue]
) -> None:
    seen: set[str] = set()
    for prop in props:
        pid = f"{owner}.{prop.id}"
        if prop.id in seen:
            issues.append(OntologyIssue(file, "duplicate property ID", module, pid))
        seen.add(prop.id)
        if prop.datatype not in DATATYPES:
            issues.append(OntologyIssue(file, f"unsupported datatype {prop.datatype!r}", module, pid))
        if prop.enum is not None:
            if prop.datatype not in ENUM_DATATYPES:
                issues.append(
                    OntologyIssue(file, f"enum not allowed for datatype {prop.datatype!r}", module, pid)
                )
            if len(set(prop.enum)) != len(prop.enum):
                issues.append(OntologyIssue(file, "enum values must be unique", module, pid))
        if prop.pattern is not None:
            if prop.datatype not in PATTERN_DATATYPES:
                issues.append(
                    OntologyIssue(file, f"pattern not allowed for datatype {prop.datatype!r}", module, pid)
                )
            _check_regex(prop.pattern, file, module, pid, issues)
            if prop.enum is not None:
                for value in prop.enum:
                    if not re.fullmatch(prop.pattern, value):
                        issues.append(
                            OntologyIssue(file, f"enum value {value!r} violates pattern", module, pid)
                        )


def _check_regex(pattern: str, file: str, module: str, oid: str, issues: list[OntologyIssue]) -> None:
    try:
        re.compile(pattern)
    except re.error as exc:
        issues.append(OntologyIssue(file, f"invalid regex {pattern!r}: {exc}", module, oid))


def _build(documents: list[tuple[str, dict]]) -> tuple[dict[str, OntologyClass], dict[str, RelationDef]]:
    issues: list[OntologyIssue] = []
    classes: dict[str, OntologyClass] = {}
    relations: dict[str, RelationDef] = {}
    declared_kind: dict[str, str | None] = {}

    for file, doc in documents:
        module = doc["module"]
        for raw in doc.get("classes", []):
            cid = raw["id"]
            if cid in classes:
                other = classes[cid]
                issues.append(
                    OntologyIssue(
                        file,
                        f"duplicate class ID (already defined in {other.source_file}, module {other.module})",
                        module,
                        cid,
                    )
                )
                continue
            props = tuple(_parse_property(p) for p in raw.get("properties", []))
            _check_properties(props, cid, file, module, issues)
            if raw.get("id_pattern"):
                _check_regex(raw["id_pattern"], file, module, cid, issues)
            declared_kind[cid] = raw.get("kind")
            classes[cid] = OntologyClass(
                id=cid,
                label=raw["label"],
                definition=raw["definition"],
                module=module,
                source_file=file,
                parent=raw.get("parent"),
                abstract=raw.get("abstract", False),
                key=raw.get("key"),
                id_pattern=raw.get("id_pattern"),
                fibo_uri=raw.get("fibo_uri"),
                synonyms=tuple(raw.get("synonyms", ())),
                properties=props,
            )
        for raw in doc.get("relations", []):
            rid = raw["id"]
            if rid in relations:
                other = relations[rid]
                issues.append(
                    OntologyIssue(
                        file,
                        f"duplicate relation ID (already defined in {other.source_file}, module {other.module})",
                        module,
                        rid,
                    )
                )
                continue
            props = tuple(_parse_property(p) for p in raw.get("properties", []))
            _check_properties(props, rid, file, module, issues)
            relations[rid] = RelationDef(
                id=rid,
                label=raw["label"],
                source=_as_tuple(raw["source"]),
                target=_as_tuple(raw["target"]),
                cardinality=raw["cardinality"],
                description=raw["description"],
                module=module,
                source_file=file,
                synonyms=tuple(raw.get("synonyms", ())),
                disjoint_with=tuple(raw.get("disjoint_with", ())),
                properties=props,
            )

    # parents and cycles
    for c in classes.values():
        if c.parent is not None and c.parent not in classes:
            issues.append(OntologyIssue(c.source_file, f"unknown parent {c.parent!r}", c.module, c.id))
    reported_cycles: set[frozenset[str]] = set()
    for c in classes.values():
        path = [c.id]
        parent = c.parent
        while parent is not None and parent in classes:
            if parent in path:
                cycle = path[path.index(parent):]
                if frozenset(cycle) not in reported_cycles:
                    reported_cycles.add(frozenset(cycle))
                    issues.append(
                        OntologyIssue(
                            c.source_file,
                            f"inheritance cycle {' -> '.join([*cycle, parent])}",
                            c.module,
                            c.id,
                        )
                    )
                break
            path.append(parent)
            parent = classes[parent].parent
    if issues:
        raise OntologyLoadError(issues)

    # resolve kind and key down the (acyclic) hierarchy
    resolved: dict[str, OntologyClass] = {}

    def resolve(cid: str) -> OntologyClass:
        if cid in resolved:
            return resolved[cid]
        c = classes[cid]
        parent = resolve(c.parent) if c.parent else None
        kind = declared_kind[cid]
        if parent is not None:
            if kind is not None and kind != parent.kind:
                issues.append(
                    OntologyIssue(
                        c.source_file,
                        f"kind {kind!r} conflicts with inherited kind {parent.kind!r} from {parent.id}",
                        c.module,
                        cid,
                    )
                )
            kind = parent.kind
        kind = kind or DEFAULT_KIND
        key = c.key if c.key is not None else (parent.key if parent else None)
        out = replace(c, kind=kind, key=key)
        resolved[cid] = out
        return out

    for cid in classes:
        resolve(cid)

    for c in resolved.values():
        if c.kind not in CLASS_KINDS:
            issues.append(OntologyIssue(c.source_file, f"unknown kind {c.kind!r}", c.module, c.id))
        inherited = {}
        for ancestor in reversed(_ancestors(resolved, c.id)):
            for prop in resolved[ancestor].properties:
                inherited[prop.id] = ancestor
        for prop in c.properties:
            if prop.id in inherited:
                issues.append(
                    OntologyIssue(
                        c.source_file,
                        f"property {prop.id!r} redefines property inherited from {inherited[prop.id]}",
                        c.module,
                        c.id,
                    )
                )
        all_props = _properties(resolved, c.id)
        if c.key is not None:
            key_prop = all_props.get(c.key)
            if key_prop is None:
                issues.append(OntologyIssue(c.source_file, f"key {c.key!r} is not a property", c.module, c.id))
            elif not key_prop.required or key_prop.cardinality != "one":
                issues.append(
                    OntologyIssue(
                        c.source_file, f"key {c.key!r} must be required with cardinality 'one'", c.module, c.id
                    )
                )
        elif not c.abstract and c.kind != "clause":
            # Clauses have no source key; their identity (contract + clause class + evidence)
            # is assigned by the extraction pipeline, not extracted by a model.
            issues.append(OntologyIssue(c.source_file, "concrete class has no key property", c.module, c.id))

    for r in relations.values():
        for end, refs in (("source", r.source), ("target", r.target)):
            for ref in refs:
                if ref not in resolved:
                    issues.append(
                        OntologyIssue(r.source_file, f"{end} endpoint references unknown class {ref!r}", r.module, r.id)
                    )
        for other in r.disjoint_with:
            if other == r.id:
                issues.append(OntologyIssue(r.source_file, "relation cannot be disjoint with itself", r.module, r.id))
            elif other not in relations:
                issues.append(
                    OntologyIssue(r.source_file, f"disjoint_with references unknown relation {other!r}", r.module, r.id)
                )
            elif r.id not in relations[other].disjoint_with:
                issues.append(
                    OntologyIssue(
                        r.source_file, f"disjoint_with {other!r} is not declared symmetrically", r.module, r.id
                    )
                )

    _check_synonyms(resolved.values(), "class", issues)
    _check_synonyms(relations.values(), "relation", issues)

    if issues:
        raise OntologyLoadError(issues)
    return resolved, relations


def _check_synonyms(defs: Iterable[OntologyClass | RelationDef], label: str, issues: list[OntologyIssue]) -> None:
    defs = list(defs)
    ids = {d.id.casefold(): d.id for d in defs}
    owner: dict[str, str] = {}
    for d in defs:
        for syn in d.synonyms:
            key = syn.casefold()
            if key in ids and ids[key] != d.id:
                issues.append(
                    OntologyIssue(d.source_file, f"synonym {syn!r} collides with {label} ID {ids[key]!r}", d.module, d.id)
                )
            elif key in owner and owner[key] != d.id:
                issues.append(
                    OntologyIssue(
                        d.source_file, f"synonym {syn!r} is also a synonym of {label} {owner[key]!r}", d.module, d.id
                    )
                )
            owner.setdefault(key, d.id)
