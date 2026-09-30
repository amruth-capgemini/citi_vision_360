"""Structured input for callers; deliberately independent of document parsing."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any


class GraphInputError(ValueError):
    """Invalid structured input; messages must not include source values."""


@dataclass(frozen=True)
class EntityRef:
    class_id: str
    identity: str | None = None
    contract_identity: str | None = None
    occurrence_id: str | None = None


@dataclass(frozen=True)
class Evidence:
    """Technical citation envelope, not new ontology business properties.

    evidence_ref points to upstream retained evidence; no raw quotes are stored here.
    A document reference must resolve within the payload's namespace.
    """

    document: EntityRef | None = None
    property_id: str | None = None
    source_ref: str | None = None
    section: str | None = None
    page: int | None = None
    chunk_id: str | None = None
    extraction_run: str | None = None
    evidence_ref: str | None = None
    confidence: float | None = None
    review_state: str = "unreviewed"
    verification_state: str | None = None


@dataclass(frozen=True)
class EntityInstance:
    class_id: str
    properties: dict[str, Any]
    identity: str | None = None
    contract_identity: str | None = None
    occurrence_id: str | None = None
    revision: int = 1
    provenance: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class RelationshipInstance:
    type: str
    source: EntityRef
    target: EntityRef
    properties: dict[str, Any] = field(default_factory=dict)
    revision: int = 1
    provenance: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class GraphPayload:
    namespace: str
    ontology_version: str
    entities: tuple[EntityInstance, ...] = ()
    relationships: tuple[RelationshipInstance, ...] = ()

    @classmethod
    def from_dict(cls, value: dict) -> GraphPayload:
        """Strict envelope decoding; semantic validation is performed by GraphMapper."""
        try:
            data = _object(cls, value)
            data["entities"] = tuple(_entity(v) for v in _array(data.get("entities", [])))
            data["relationships"] = tuple(_relationship(v) for v in _array(data.get("relationships", [])))
            return cls(**data)
        except (TypeError, KeyError, AttributeError):
            raise GraphInputError("Malformed graph payload envelope") from None


def _object(model, value):
    if not isinstance(value, dict) or set(value) - {f.name for f in fields(model)}:
        raise GraphInputError("Unknown fields or invalid object in graph payload")
    return dict(value)


def _array(value):
    if not isinstance(value, list):
        raise GraphInputError("Expected a JSON array")
    return value


def _evidence(value):
    data = _object(Evidence, value)
    if data.get("document") is not None:
        data["document"] = EntityRef(**_object(EntityRef, data["document"]))
    return Evidence(**data)


def _entity(value):
    data = _object(EntityInstance, value)
    data["provenance"] = tuple(_evidence(v) for v in _array(data.get("provenance", [])))
    return EntityInstance(**data)


def _relationship(value):
    data = _object(RelationshipInstance, value)
    for end in ("source", "target"):
        data[end] = EntityRef(**_object(EntityRef, data[end]))
    data["provenance"] = tuple(_evidence(v) for v in _array(data.get("provenance", [])))
    return RelationshipInstance(**data)
