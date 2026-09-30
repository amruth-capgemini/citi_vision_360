"""The sole identity construction boundary for nodes and logical relationships."""

import hashlib
import json
import re

from ..ontology import OntologyRegistry, OntologyValidationError
from .models import EntityRef, GraphInputError


def text_id(value, name):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise GraphInputError(f"{name} must be a nonblank string without surrounding whitespace")
    return value


def digest(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def canonical_identity(registry: OntologyRegistry, namespace: str, ref: EntityRef) -> str:
    text_id(namespace, "namespace")
    if not isinstance(ref, EntityRef):
        raise GraphInputError("Expected EntityRef")
    text_id(ref.class_id, "class_id")
    try:
        cls = registry.validate_class(ref.class_id, allow_abstract=False)
    except OntologyValidationError:
        raise GraphInputError("Unknown or abstract entity class") from None
    if cls.kind == "clause":
        if ref.identity is not None:
            raise GraphInputError("Clauses use contract_identity and occurrence_id, not identity")
        occurrence = text_id(ref.occurrence_id, "occurrence_id")
        contract = text_id(ref.contract_identity, "contract_identity")
        canonical_identity(registry, namespace, EntityRef("Contract", contract))
        return digest("clause", namespace, contract, cls.id, occurrence)
    if ref.contract_identity is not None or ref.occurrence_id is not None:
        raise GraphInputError("Clause identity fields are only valid for clause classes")
    if cls.key is None:
        raise GraphInputError("Entity class has no supported identity strategy")
    identity = text_id(ref.identity, "identity")
    try:
        registry.validate_property(cls.id, cls.key, identity)
    except OntologyValidationError:
        raise GraphInputError("Invalid canonical identity value") from None
    if cls.id_pattern and re.fullmatch(cls.id_pattern, identity) is None:
        raise GraphInputError("Canonical identity does not match ontology ID pattern")
    return digest("entity", namespace, cls.id, identity)


def relationship_identity(namespace, relation_type, source_key, target_key):
    # One logical edge per directed tuple. Multiple citations belong in provenance.
    return digest("relationship", namespace, relation_type, source_key, target_key)
