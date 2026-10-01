"""What kinds of data exist, from the ontology: the digest the supervisor reads to understand a
question, and the deterministic checks behind "is this recorded at all?" and "where would it be?".

The ontology is the model of the data (`ontology/*.yaml`). A concept such as
``Application.name`` is answerable when its class declares the property; a class or property
the ontology does not declare is not recorded in any source, so no tool or query can return it.
"""

from collections import deque
from functools import lru_cache
import re

from ..ontology import OntologyRegistry, UnknownClassError

# Catalog metadata (datasets, fields, source systems) describes the data; it is not business data.
METADATA_MODULES = ("data",)
KINDS = ("entity", "fact", "clause")
CONCEPT = re.compile(r"^\s*([A-Za-z][\w ]*?)\s*(?:\.\s*([A-Za-z][\w ]*))?\s*$")


@lru_cache(maxsize=1)
def default_registry():
    return OntologyRegistry.load()


class DataDigest:
    def __init__(self, registry=None):
        self.registry = registry or default_registry()
        classes = self.registry.classes
        self.types = {c.id: c for c in classes.values()
                      if c.kind in KINDS and not c.abstract and c.module not in METADATA_MODULES}
        self.relations = [(s, r.id, t) for r in self.registry.relations.values() for s in r.source for t in r.target
                          if s in self.types and t in self.types]

    def summary(self):
        """Compact description for the model: each type, its ID format, synonyms and the attributes it holds."""
        types = []
        for c in self.types.values():
            attributes = [p for p, d in self.registry.properties_for_class(c.id).items() if not d.pii]
            entry = {"type": c.id, "label": c.label, "attributes": attributes}
            if c.id_pattern:
                entry["id_format"] = c.id_pattern.strip("^$").replace("\\d", "9").replace("{3}", "").replace("{2}", "")
            if c.synonyms:
                entry["also_called"] = list(c.synonyms)
            types.append(entry)
        return {"types": types, "relations": [f"{s} -{r}-> {t}" for s, r, t in self.relations]}

    def entity_type(self, name):
        """The ontology type for a type name or synonym ('Organization' -> 'OrganizationUnit'), or None."""
        if not isinstance(name, str) or not name.strip():
            return None
        try:
            found = self.registry.resolve_class(name.strip()).id
        except UnknownClassError:
            found = next((t for t in self.types if t.casefold() == name.strip().casefold()), None)
        return found if found in self.types else None

    def classify(self, value):
        """Business types whose ID format matches a raw identifier ('ORG-01' -> ['OrganizationUnit'])."""
        return [t for t in self.registry.classify_identifier(value.strip().upper()) if t in self.types]

    def identifiers(self, text):
        """Every token in the text that is a business ID, with its type, in order of appearance."""
        found = {}
        for token in re.findall(r"\b[A-Z]{1,6}(?:-[A-Z]{1,4})?-\d{2,6}(?:-[A-Z]?\d{1,6})*\b", text or "", re.I):
            types = self.classify(token)
            if types:
                found.setdefault(token.upper(), types[0])
        return [{"id": k, "type": v} for k, v in found.items()]

    def _split(self, text):
        """('Application', 'name') from 'Application.name', 'application names' or 'Applications'."""
        match = CONCEPT.match(text or "")
        if not match:
            return None, None
        if match.group(2):
            words, attribute = [match.group(1)], match.group(2).strip()
        else:
            words, attribute = [match.group(1)], None
            parts = match.group(1).split()
            # 'organization unit name': the longest leading words that name a type, the rest the attribute.
            for cut in range(len(parts), 0, -1):
                head = " ".join(parts[:cut])
                if self.entity_type(head) or self.entity_type(head.rstrip("s")):
                    words, attribute = [head], " ".join(parts[cut:]) or None
                    break
        name = words[0]
        found = self.entity_type(name) or self.entity_type(name.rstrip("s"))
        if attribute:
            attribute = attribute.strip().replace(" ", "_")
        return found, attribute

    def concept(self, text):
        """Check one requested concept ('Application.name', 'organization name') against the ontology.

        Returns {"concept", "type", "attribute", "recorded", "restricted"}. recorded is False only
        when the type is known and declares no such attribute, so no source can hold it; it is None
        when the type itself is not recognised (nothing is claimed either way)."""
        found, attribute = self._split(text)
        if found is None:
            return {"concept": text, "type": None, "attribute": attribute, "recorded": None, "restricted": False}
        if attribute is None:
            return {"concept": found, "type": found, "attribute": None, "recorded": True, "restricted": False}
        prop = None
        for candidate in dict.fromkeys((attribute, attribute.casefold(), attribute.casefold().removesuffix("s"))):
            prop = prop or self.registry.property_for_field(found, candidate)
        if prop is None and attribute.casefold().removesuffix("s") == "name":
            # 'name' is how people ask for a display name, whatever the attribute is called.
            props = self.registry.properties_for_class(found)
            prop = next((props[p] for p in props if p.endswith("name")), None)
        if prop is None:
            # 'Vendor.owner' asks for a related entity, not an attribute: that type is the concept.
            related = self.entity_type(attribute.replace("_", " ")) or self.entity_type(attribute.rstrip("s"))
            if related and related != found:
                return {"concept": related, "type": related, "attribute": None, "recorded": True, "restricted": False}
            return {"concept": f"{found}.{attribute}", "type": found, "attribute": attribute, "recorded": False, "restricted": False}
        return {"concept": f"{found}.{prop.id}", "type": found, "attribute": prop.id, "recorded": True, "restricted": prop.pii}

    def path(self, source, target):
        """Shortest relation path between two types as 'A -R-> B <-S- C', following relation direction
        when that links them and ignoring it otherwise; None if unlinked."""
        if source not in self.types or target not in self.types:
            return None
        if source == target:
            return source
        return self._search(source, target, directed=True) or self._search(source, target, directed=False)

    def _search(self, source, target, *, directed):
        edges = {}
        for s, r, t in self.relations:
            edges.setdefault(s, []).append((t, f" -{r}-> "))
            if not directed:
                edges.setdefault(t, []).append((s, f" <-{r}- "))
        seen, queue = {source}, deque([(source, source)])
        while queue:
            node, text = queue.popleft()
            for nxt, arrow in edges.get(node, ()):
                if nxt in seen:
                    continue
                if nxt == target:
                    return text + arrow + nxt
                seen.add(nxt)
                queue.append((nxt, text + arrow + nxt))
        return None

    def label(self, type_id):
        c = self.types.get(type_id)
        return c.label if c else type_id
