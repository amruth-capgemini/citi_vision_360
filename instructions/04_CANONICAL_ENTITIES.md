# Phase 4 — Canonical entities from approved tables

## Goal

Turn approved reference rows into stable business entities. Give later contract extraction an authoritative vendor identity to match.

## Entry

Phase 3 approved vendor table/column bindings exist. Confirm safe row reading and source ID/tenant boundaries in the actual ingestion system.

## Deliverables

`services/ontology/entity_materializer.py`, `normalize.py`, basic `entity_resolver.py` helpers and tests for normalization, idempotence and identity conflicts.

## Workflow

Materialize only a reference table with an approved class, stable key and display-name binding. Read the data through the existing connector/query safety layer. For each distinct vendor key, upsert the canonical `Vendor` entity and record its source row key; add aliases for `external_id`, name and legal name when present. Preserve provenance and version, batch safely, and make reruns idempotent. Define behavior for source record rename and deletion without silently erasing reviewed history.

Normalize Unicode, case, surrounding/repeated whitespace and punctuation where appropriate. Handle legal suffixes as a cautious comparison feature, not proof of identity; abbreviations such as `AWS` need explicit alias or other evidence. Priority is: stable enterprise ID in the same namespace, exact normalized alias, exact canonical name, then proposed fuzzy/vector candidates. Do not merge on string or embedding similarity alone. Detect a stable-ID collision with different known entities and route it to review.

Use an identity key containing the tenant/source namespace, entity class and stable ID. If multiple sources use different IDs for the same real vendor, record a reviewed alias or crosswalk rather than assuming equality. Store links to physical source rows needed for `RECORDED_IN` projection and Text-to-SQL later.

## Tests and exit gate

Materialize V001 Amazon Web Services and V002 Acme Technologies; rerun and verify two entities, not four. Check aliases, source lineage, changed display name and conflicting IDs. Show that an unapproved binding or ambiguous name does not create/merge an entity. Report commands/results; stop before Phase 5.
