"""Pure registry-driven mapping; no driver or database imports."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import re

from ..ontology import OntologyRegistry, OntologyValidationError
from ..ontology.registry import validate_property_value
from .identity import canonical_identity, relationship_identity, text_id
from .models import EntityInstance, EntityRef, Evidence, GraphInputError, GraphPayload, RelationshipInstance


@dataclass(frozen=True)
class NodeRow:
    key: str
    class_id: str
    labels: tuple[str, ...]
    properties: dict
    references: tuple[str, ...]


@dataclass(frozen=True)
class EdgeRow:
    key: str
    type: str
    source: str
    target: str
    properties: dict
    references: tuple[str, ...]


@dataclass(frozen=True)
class MappedPayload:
    namespace: str
    ontology_version: str
    nodes: tuple[NodeRow, ...]
    edges: tuple[EdgeRow, ...]
    input_entities: int
    input_relationships: int


def identifier(value):
    """Defense in depth for schema-generated Cypher identifiers, never data values."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise GraphInputError("Unsafe ontology identifier")
    return f"`{value}`"


def _revision(value):
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise GraphInputError("revision must be a positive signed 64-bit integer")


def _graph_value(value):
    values = value if isinstance(value, list) else [value]
    for item in values:
        if isinstance(value, list) and item is None:
            raise GraphInputError("Property lists cannot contain null")
        if type(item) is int and not -(2**63) <= item < 2**63:
            raise GraphInputError("Integer exceeds Neo4j signed 64-bit range")
        if type(item) is float and not math.isfinite(item):
            raise GraphInputError("Numeric properties must be finite")


class GraphMapper:
    def __init__(self, registry: OntologyRegistry):
        self.registry = registry

    def properties(self, definitions, values):
        if not isinstance(values, dict):
            raise GraphInputError("properties must be an object")
        mapped = {}
        for name, value in values.items():
            prop = definitions.get(name)
            if prop is None:
                matches = [p for p in definitions.values() if name in p.synonyms]
                if len(matches) != 1:
                    raise GraphInputError("Unknown or ambiguous ontology property")
                prop = matches[0]
            if prop.id in mapped:
                raise GraphInputError("Multiple input fields map to the same ontology property")
            if prop.id.startswith("_kg_"):
                raise GraphInputError("Ontology property conflicts with graph metadata")
            try:
                validate_property_value(prop, value)
            except OntologyValidationError:
                raise GraphInputError("Property violates ontology datatype or constraint") from None
            _graph_value(value)
            mapped[prop.id] = list(value) if isinstance(value, list) else value
        if any(p.required and (p.id not in mapped or mapped[p.id] is None) for p in definitions.values()):
            raise GraphInputError("Required ontology property missing")
        # Neo4j represents null as absent. Full SET replacement removes old optional fields.
        return {k: v for k, v in mapped.items() if v is not None}

    def provenance(self, namespace, citations, definitions):
        if not isinstance(citations, (tuple, list)):
            raise GraphInputError("provenance must be a sequence")
        serialized, refs = [], []
        for evidence in citations:
            if not isinstance(evidence, Evidence):
                raise GraphInputError("Expected Evidence")
            if evidence.review_state not in ("unreviewed", "proposed", "approved", "rejected"):
                raise GraphInputError("Invalid evidence review_state")
            if evidence.property_id is not None and evidence.property_id not in definitions:
                raise GraphInputError("Evidence property_id must be a canonical ontology property")
            for name in ("source_ref", "section", "chunk_id", "extraction_run", "evidence_ref", "verification_state"):
                value = getattr(evidence, name)
                if value is not None:
                    text_id(value, name)
            if evidence.page is not None and (type(evidence.page) is not int or not 1 <= evidence.page < 2**63):
                raise GraphInputError("Evidence page must be a positive integer or null")
            if evidence.confidence is not None and (
                type(evidence.confidence) not in (int, float) or not math.isfinite(evidence.confidence)
                or not 0 <= evidence.confidence <= 1
            ):
                raise GraphInputError("Evidence confidence must be between zero and one")
            if evidence.document is not None:
                key = canonical_identity(self.registry, namespace, evidence.document)
                if self.registry.get_class(evidence.document.class_id).kind != "document":
                    raise GraphInputError("Evidence document must reference a document class")
                refs.append(key)
            elif any(v is not None for v in (evidence.page, evidence.section, evidence.chunk_id)):
                raise GraphInputError("Document location metadata requires a document reference")
            if not any((evidence.document, evidence.source_ref, evidence.evidence_ref)):
                raise GraphInputError("Evidence requires a document, source_ref or evidence_ref")
            serialized.append(json.dumps(asdict(evidence), sort_keys=True, separators=(",", ":")))
        # Order and duplicate citations cannot change the persisted record.
        return json.dumps([json.loads(v) for v in sorted(set(serialized))], sort_keys=True, separators=(",", ":")), tuple(sorted(set(refs)))

    def node(self, namespace, entity):
        if not isinstance(entity, EntityInstance):
            raise GraphInputError("Expected EntityInstance")
        text_id(entity.class_id, "class_id")
        try:
            cls = self.registry.validate_class(entity.class_id, allow_abstract=False)
        except OntologyValidationError:
            raise GraphInputError("Unknown or abstract entity class") from None
        definitions = self.registry.properties_for_class(cls.id)
        props = self.properties(definitions, entity.properties)
        identity = entity.identity
        if cls.key is not None:
            if identity is None:
                identity = props[cls.key]
            if identity != props[cls.key]:
                raise GraphInputError("identity must equal the declared ontology key property")
        ref = EntityRef(cls.id, identity, entity.contract_identity, entity.occurrence_id)
        key = canonical_identity(self.registry, namespace, ref)
        _revision(entity.revision)
        evidence, refs = self.provenance(namespace, entity.provenance, definitions)
        if cls.kind == "clause":
            refs = tuple(sorted(set(refs) | {canonical_identity(self.registry, namespace, EntityRef("Contract", entity.contract_identity))}))
        labels = ("CitiKGEntity", cls.id, *self.registry.ancestors(cls.id))
        for label in labels:
            identifier(label)
        props.update({
            "_kg_key": key, "_kg_namespace": namespace, "_kg_class": cls.id,
            "_kg_identity": identity, "_kg_contract_identity": entity.contract_identity,
            "_kg_occurrence_id": entity.occurrence_id, "_kg_revision": entity.revision,
            "_kg_ontology_version": self.registry.ontology_version, "_kg_provenance": evidence,
        })
        return NodeRow(key, cls.id, labels, {k: v for k, v in props.items() if v is not None}, refs)

    def edge(self, namespace, relationship):
        if not isinstance(relationship, RelationshipInstance):
            raise GraphInputError("Expected RelationshipInstance")
        source = canonical_identity(self.registry, namespace, relationship.source)
        target = canonical_identity(self.registry, namespace, relationship.target)
        text_id(relationship.type, "relationship type")
        try:
            rel = self.registry.resolve_relation(relationship.type)
            self.registry.validate_relation(rel.id, relationship.source.class_id, relationship.target.class_id)
        except OntologyValidationError:
            raise GraphInputError("Unknown relationship or invalid directed endpoints") from None
        identifier(rel.id)
        if rel.id == "HAS_CLAUSE" and relationship.source.identity != relationship.target.contract_identity:
            raise GraphInputError("HAS_CLAUSE must reference the clause's identity contract")
        definitions = {p.id: p for p in rel.properties}
        props = self.properties(definitions, relationship.properties)
        _revision(relationship.revision)
        evidence, refs = self.provenance(namespace, relationship.provenance, definitions)
        key = relationship_identity(namespace, rel.id, source, target)
        props.update({"_kg_key": key, "_kg_namespace": namespace,
                      "_kg_revision": relationship.revision,
                      "_kg_ontology_version": self.registry.ontology_version, "_kg_provenance": evidence})
        return EdgeRow(key, rel.id, source, target, props, refs)

    def map(self, payload):
        if not isinstance(payload, GraphPayload):
            raise GraphInputError("Expected GraphPayload")
        text_id(payload.namespace, "namespace")
        if payload.ontology_version != self.registry.ontology_version:
            raise GraphInputError("Payload ontology_version differs from loaded registry")
        if not isinstance(payload.entities, (tuple, list)) or not isinstance(payload.relationships, (tuple, list)):
            raise GraphInputError("entities and relationships must be sequences")
        nodes = self._deduplicate(self.node(payload.namespace, e) for e in payload.entities)
        edges = self._deduplicate(self.edge(payload.namespace, e) for e in payload.relationships)
        return MappedPayload(payload.namespace, payload.ontology_version, nodes, edges,
                             len(payload.entities), len(payload.relationships))

    @staticmethod
    def _deduplicate(rows):
        found = {}
        for row in rows:
            if row.key in found and found[row.key] != row:
                raise GraphInputError("Conflicting records share a canonical identity")
            found[row.key] = row
        return tuple(found[k] for k in sorted(found))
