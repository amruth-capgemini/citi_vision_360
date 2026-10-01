"""Build the catalog GraphPayload: systems, datasets, fields, domains, joins and anchors.

Only metadata is emitted. The only source values in the payload are anchor keys and
names from the canonical master, value sets of small non-sensitive category fields,
and value patterns. HAS_RECORDS_FOR links a dataset to an anchor only when the
anchor's key occurs in one of the dataset's auto-mapped identifier fields. Fields link
to business Concepts (MAPS_TO) and to inferred foreign keys (REFERENCES); datasets get a
derived JOINABLE_WITH summary of those references.
"""

from dataclasses import replace
import hashlib
import json
import re

from ..knowledge_graph.models import EntityInstance, EntityRef, Evidence, GraphInputError, GraphPayload, RelationshipInstance
from .classifier import concept_name, humanize
from .ontology import CATALOG_NAMESPACE, DOMAINS, SCHEMA_DOMAINS
from .relationships import dataset_links, discover_references

_ANCHORS = {("Vendor", "vendor_id"): "VendorAnchor", ("Contract", "contract_id"): "ContractAnchor"}
_DOMAIN_BASIS = ("source_system", "fields", "llm")


def system_id(schema):
    identity = "PG-" + re.sub(r"[^A-Z]+", "-", schema.upper()).strip("-")
    if not re.fullmatch(r"PG-[A-Z-]+", identity):
        raise GraphInputError("Schema name cannot form a source system identifier")
    return identity


def _clean(properties):
    return {k: v for k, v in properties.items() if v is not None and v != []}


def build_payload(harvest, classification, registry, business, *, namespace=CATALOG_NAMESPACE):
    """``registry`` is the catalog registry (the payload is pinned to its ontology_version);
    ``business`` is the business ontology the field mappings and concepts refer to."""
    entities, relationships = [], []
    concepts = set()
    for schema, comment in sorted(harvest.schemas.items()):
        domain = SCHEMA_DOMAINS.get(schema)
        entities.append(EntityInstance("SourceSystem", _clean({
            "source_system_id": system_id(schema), "name": schema, "description": comment,
            "source_class": DOMAINS[domain][1] if domain else None})))
    for domain_id, (name, _, description) in DOMAINS.items():
        entities.append(EntityInstance("BusinessDomain", {"domain_id": domain_id, "name": name, "description": description}))
    anchor_keys = {"VendorAnchor": set(), "ContractAnchor": set()}
    for anchor in harvest.anchors:
        entities += [EntityInstance("VendorAnchor", _clean({"vendor_id": anchor["vendor_id"], "name": anchor["vendor_name"]})),
                     EntityInstance("ContractAnchor", _clean({"contract_id": anchor["contract_id"], "description": anchor["contract_description"]}))]
        anchor_keys["VendorAnchor"].add(anchor["vendor_id"])
        anchor_keys["ContractAnchor"].add(anchor["contract_id"])
        relationships.append(RelationshipInstance("ANCHOR_PARTY_TO", EntityRef("VendorAnchor", anchor["vendor_id"]),
                                                  EntityRef("ContractAnchor", anchor["contract_id"])))
    for table in harvest.tables:
        dataset = classification.datasets[table.dataset_id]
        ref = EntityRef("Dataset", table.dataset_id)
        as_of = next((c.min_value for c in table.columns if c.name == "As_Of_Date" and c.dates and c.min_value == c.max_value), None)
        entities.append(EntityInstance("Dataset", _clean({
            "dataset_id": table.dataset_id, "grain": dataset.grain, "physical_name": table.dataset_id,
            "description": table.comment, "summary": dataset.summary, "row_count": table.row_count,
            "primary_key": list(table.primary_key), "as_of": as_of, "review_state": dataset.review_state})))
        relationships.append(RelationshipInstance("PUBLISHES", EntityRef("SourceSystem", system_id(table.schema)), ref))
        for domain_id, basis in sorted(dataset.domains.items()):
            relationships.append(RelationshipInstance("IN_DOMAIN", ref, EntityRef("BusinessDomain", domain_id), {"basis": basis}))
        linked = {}
        for column in sorted(table.columns, key=lambda c: c.position):
            f = dataset.fields[column.name]
            field_id = f"{table.dataset_id}.{column.name}"
            entities.append(EntityInstance("DataField", _clean({
                "field_id": field_id, "path": column.name, "source_datatype": column.data_type, "position": column.position,
                "business_name": f.business_name or humanize(column.name),
                "description": f.description, "semantic_role": f.semantic_role, "ontology_class": f.ontology_class,
                "ontology_property": f.ontology_property, "null_pct": float(column.null_pct), "distinct_count": column.distinct_count,
                "value_pattern": f.value_pattern,
                "min_value": column.min_value if f.semantic_role == "date" else None,
                "max_value": column.max_value if f.semantic_role == "date" else None,
                "value_set": list(f.value_set) if f.value_set and not f.masked else None,
                "name_collision": f.name_collision, "masked": f.masked,
                "confidence": f.confidence, "review_state": f.review_state})))
            relationships.append(RelationshipInstance("HAS_FIELD", ref, EntityRef("DataField", field_id)))
            if f.mapping:
                concepts.add((f.ontology_class, f.ontology_property))
                relationships.append(RelationshipInstance("MAPS_TO", EntityRef("DataField", field_id), EntityRef("Concept", f.mapping),
                                                          {"basis": "classifier" if f.basis == "classifier" else "llm"}))
            anchor_class = _ANCHORS.get((f.ontology_class, f.ontology_property))
            if anchor_class and f.review_state == "auto" and not column.distinct_truncated:
                for key in sorted(set(column.distinct_values) & anchor_keys[anchor_class]):
                    linked.setdefault((anchor_class, key), column.name)  # first field by position names the link
        for (anchor_class, key), via in sorted(linked.items()):
            relationships.append(RelationshipInstance("HAS_RECORDS_FOR", ref, EntityRef(anchor_class, key), {"via_field": via}))
    for class_id, property_id in sorted(concepts):
        prop, cls = business.get_property(class_id, property_id), business.get_class(class_id)
        entities.append(EntityInstance("Concept", {
            "concept_id": f"{class_id}.{property_id}", "business_name": concept_name(business, class_id, property_id),
            "definition": " ".join(prop.description.split()), "datatype": prop.datatype, "is_key": property_id == cls.key}))
    references = discover_references(harvest, classification, business)
    for r in references:
        relationships.append(RelationshipInstance("REFERENCES", EntityRef("DataField", r.source), EntityRef("DataField", r.target), {
            "containment_pct": r.containment_pct, "basis": r.basis, "confidence": r.confidence, "review_state": r.review_state}))
    for (source, target), keys in dataset_links(references).items():
        relationships.append(RelationshipInstance("JOINABLE_WITH", EntityRef("Dataset", source), EntityRef("Dataset", target), {"keys": keys}))
    run_id = "harvest-" + _digest(entities, relationships)[:16]
    evidence = lambda source: (Evidence(source_ref=source, extraction_run=run_id),)
    entities = [replace(e, provenance=evidence(_source(e))) for e in entities]
    relationships = [replace(r, provenance=evidence("postgres:catalog")) for r in relationships]
    return GraphPayload(namespace, registry.ontology_version, tuple(entities), tuple(relationships))


def _source(entity):
    p = entity.properties
    if entity.class_id == "Dataset":
        return f"postgres:{p['dataset_id']}"
    if entity.class_id == "DataField":
        return "postgres:" + p["field_id"].rsplit(".", 1)[0]
    if entity.class_id == "Concept":
        return "ontology:" + p["concept_id"]
    if entity.class_id in ("VendorAnchor", "ContractAnchor"):
        return "postgres:clm.canonical_vendor_master"
    return "postgres:catalog"


def _digest(entities, relationships):
    content = [[e.class_id, e.properties] for e in entities] + [[r.type, r.source.identity, r.target.identity, r.properties] for r in relationships]
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def summarize(payload):
    """Counts for dry runs and reports."""
    by_class, by_type, fields = {}, {}, [e for e in payload.entities if e.class_id == "DataField"]
    for e in payload.entities:
        by_class[e.class_id] = by_class.get(e.class_id, 0) + 1
    for r in payload.relationships:
        by_type[r.type] = by_type.get(r.type, 0) + 1
    states = {}
    for f in fields:
        states[f.properties.get("review_state")] = states.get(f.properties.get("review_state"), 0) + 1
    references = [r for r in payload.relationships if r.type == "REFERENCES"]
    contracts = {r.target.identity for r in payload.relationships if r.type == "HAS_RECORDS_FOR" and r.target.class_id == "ContractAnchor"}
    return {"namespace": payload.namespace, "ontology_version": payload.ontology_version, "entities": by_class,
            "relationships": by_type, "mapped_fields": sum("ontology_class" in f.properties for f in fields),
            "field_review_states": states, "name_collisions": sorted(f.properties["field_id"] for f in fields if f.properties.get("name_collision")),
            "masked_fields": sorted(f.properties["field_id"] for f in fields if f.properties.get("masked")),
            "linked_contracts": len(contracts),
            "references": {"auto": sum(r.properties["review_state"] == "auto" for r in references),
                           "unreviewed": sum(r.properties["review_state"] == "unreviewed" for r in references)}}
