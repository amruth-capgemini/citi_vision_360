"""Idempotent schema for Neo4j 5.7+ (relationship uniqueness support)."""

from dataclasses import dataclass

from .mapping import identifier
from .models import GraphInputError


@dataclass(frozen=True)
class SchemaRule:
    name: str
    query: str
    type: str
    label: str
    properties: tuple[str, ...]


def schema_rules(registry):
    rules = []
    for label, prop, name in (("CitiKGEntity", "_kg_key", "citi_kg_entity_key"),
                              ("CitiKGScope", "namespace", "citi_kg_scope_key")):
        rules.append(SchemaRule(name,
            f"CREATE CONSTRAINT {identifier(name)} IF NOT EXISTS FOR (n:{identifier(label)}) "
            f"REQUIRE n.{identifier(prop)} IS UNIQUE", "UNIQUENESS", label, (prop,)))
    for rid in sorted(registry.relations):
        name = f"citi_kg_{rid.lower()}_key"
        rules.append(SchemaRule(name,
            f"CREATE CONSTRAINT {identifier(name)} IF NOT EXISTS FOR ()-[r:{identifier(rid)}]-() "
            "REQUIRE r._kg_key IS UNIQUE", "RELATIONSHIP_UNIQUENESS", rid, ("_kg_key",)))
    rules.append(SchemaRule("citi_kg_scope_class",
        "CREATE INDEX citi_kg_scope_class IF NOT EXISTS FOR (n:CitiKGEntity) ON (n._kg_namespace, n._kg_class)",
        "INDEX", "CitiKGEntity", ("_kg_namespace", "_kg_class")))
    # One natural-key lookup index per concrete class, including inherited document_id.
    for cid, cls in sorted(registry.classes.items()):
        if not cls.abstract and cls.key:
            name = f"citi_kg_lookup_{cid.lower()}"
            rules.append(SchemaRule(name,
                f"CREATE INDEX {identifier(name)} IF NOT EXISTS FOR (n:{identifier(cid)}) "
                f"ON (n._kg_namespace, n.{identifier(cls.key)})", "INDEX", cid, ("_kg_namespace", cls.key)))
    return tuple(rules)


CONSTRAINTS = "SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties RETURN name, type, labelsOrTypes, properties"


def require_constraints(tx, registry):
    actual = {r["name"]: r for r in tx.run(CONSTRAINTS)}
    for rule in schema_rules(registry):
        if rule.type == "INDEX":
            continue
        row = actual.get(rule.name)
        # Cypher 25 renamed the SHOW CONSTRAINTS uniqueness type values.
        accepted_types = {
            "UNIQUENESS": {"UNIQUENESS", "NODE_PROPERTY_UNIQUENESS"},
            "RELATIONSHIP_UNIQUENESS": {"RELATIONSHIP_UNIQUENESS", "RELATIONSHIP_PROPERTY_UNIQUENESS"},
        }[rule.type]
        if (not row or row["type"] not in accepted_types or list(row["labelsOrTypes"]) != [rule.label]
                or tuple(row["properties"]) != rule.properties):
            raise GraphInputError("Required uniqueness schema missing or incompatible; run init-schema")


def ensure_schema(client, registry):
    # DDL is explicit, separate from data ingestion; each statement can safely be retried.
    for rule in schema_rules(registry):
        client.write(lambda tx, query: tx.run(query).consume(), rule.query)
    client.read(require_constraints, registry)
    return len(schema_rules(registry))
