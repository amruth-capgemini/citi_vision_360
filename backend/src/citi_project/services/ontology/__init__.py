"""Governed ontology registry: allowed classes, properties and relations for the semantic layer."""

from .models import (
    InvalidRelationEndpointError,
    InvalidValueError,
    OntologyClass,
    OntologyError,
    OntologyIssue,
    OntologyLoadError,
    OntologyValidationError,
    PropertyDef,
    RelationDef,
    UnknownClassError,
    UnknownPropertyError,
    UnknownRelationError,
)
from .registry import OntologyRegistry, default_ontology_dir

__all__ = [
    "InvalidRelationEndpointError",
    "InvalidValueError",
    "OntologyClass",
    "OntologyError",
    "OntologyIssue",
    "OntologyLoadError",
    "OntologyRegistry",
    "OntologyValidationError",
    "PropertyDef",
    "RelationDef",
    "UnknownClassError",
    "UnknownPropertyError",
    "UnknownRelationError",
    "default_ontology_dir",
]
