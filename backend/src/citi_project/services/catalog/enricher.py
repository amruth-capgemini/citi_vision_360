"""Optional model enrichment for what the classifier leaves unresolved.

The model describes datasets and columns and proposes ontology mappings, choosing only
from enums built from the registry. It never sees masked values, never overrides a
classifier mapping, and every proposal it makes is stored as ``unreviewed``.
"""

from copy import deepcopy
import json

from ..agents.contracts import AgentError, ModelError, obj, validate
from .ontology import DOMAINS

PROMPT_VERSION = "catalog-enricher-v2"
PROMPT = """You describe tables in an enterprise metadata catalog for vendor information.
You receive one table's name, catalog comment and column profiles, including each column's
catalog comment where one exists; comments are authoritative about meaning. Data rows are normally not
included; sample_rows is empty unless an operator enabled it.
- summary: two or three sentences on what the table holds and which business questions it answers.
- grain: what exactly one row represents, in one short phrase.
- domains: every business domain whose information the table carries.
- For each requested column: a short business name (as a business reader would say it, for
  example "Remaining-year forecast" for Col_2026_YTP), a one-sentence business description, and
  the ontology property it carries ("Class.property") or "none" when no listed property fits;
  confidence from 0 to 1.
Rules: describe meaning, not values; never copy data values into names or descriptions; choose a mapping
only when the column clearly carries that property, otherwise "none"; values marked <masked> are
withheld on purpose. Profiles describe all rows."""
MAX_DESCRIPTION = 500
MAX_BUSINESS_NAME = 120
MAX_MODEL_CONFIDENCE = 0.95  # 1.0 is reserved for deterministic classifier matches
MIN_MODEL_CONFIDENCE = 0.6  # weaker proposals are not recorded as mappings
METADATA_MODULES = {"data"}  # catalog metadata classes (Dataset, DataField ...) are never business mappings
_MAX_SAMPLE_TEXT = 80


class CatalogEnricher:
    def __init__(self, model, registry, classifier, *, columns_per_call=40):
        if type(columns_per_call) is not int or not 1 <= columns_per_call <= 80:
            raise ValueError("columns_per_call must be between 1 and 80")
        self.model, self.registry, self.classifier = model, registry, classifier
        self.columns_per_call = columns_per_call
        self.mappings = sorted(f"{cid}.{pid}" for cid, cls in registry.classes.items()
                               if not cls.abstract and cls.module not in METADATA_MODULES
                               for pid in registry.properties_for_class(cid))

    def schema(self, names, with_dataset):
        column = obj({"name": {"type": "string", "enum": names}, "business_name": {"type": "string"},
                      "description": {"type": "string"},
                      "ontology_mapping": {"type": "string", "enum": ["none", *self.mappings]},
                      "confidence": {"type": "number"}})
        properties = {"columns": {"type": "array", "items": column}}
        if with_dataset:
            properties = {"summary": {"type": "string"}, "grain": {"type": "string"},
                          "domains": {"type": "array", "items": {"type": "string", "enum": list(DOMAINS)}}, **properties}
        return obj(properties)

    def payload(self, table, dataset, names):
        fields = dataset.fields
        profiles = []
        for column in table.columns:
            f = fields[column.name]
            profiles.append({"name": column.name, "comment": column.comment, "type": column.data_type, "role": f.semantic_role,
                             "null_pct": column.null_pct, "distinct_count": column.distinct_count,
                             "value_pattern": f.value_pattern, "value_set": list(f.value_set) if f.value_set else None,
                             "mapping": f.mapping, "business_name": f.business_name, "name_collision": f.name_collision, "masked": f.masked})
        shown = set(names) | {c.name for c in table.columns if fields[c.name].semantic_role == "identifier"}
        samples = [{k: ("<masked>" if fields[k].masked else (v or "")[:_MAX_SAMPLE_TEXT]) for k, v in row.items() if k in shown}
                   for row in table.sample_rows]
        return {"prompt_version": PROMPT_VERSION, "dataset": table.dataset_id, "table_comment": table.comment,
                "row_count": table.row_count, "primary_key": list(table.primary_key), "deterministic_grain": dataset.grain,
                "columns": profiles, "sample_rows": samples, "describe_columns": names}

    def enrich(self, harvest, classification):
        """Return (new classification, failures). A failed table keeps its deterministic classification."""
        result, failures = deepcopy(classification), []
        for table in harvest.tables:
            dataset = result.datasets[table.dataset_id]
            pending = [c.name for c in table.columns if dataset.fields[c.name].basis != "classifier"
                       or dataset.fields[c.name].name_collision]
            chunks = [pending[i:i + self.columns_per_call] for i in range(0, len(pending), self.columns_per_call)] or [[]]
            proposals = []
            for i, names in enumerate(chunks):
                try:
                    proposals.append(self._call(table, dataset, names, i == 0))
                except (AgentError, ModelError):
                    # A failed batch keeps its columns' deterministic classification; other batches still apply.
                    proposals.append(None)
                    failures.append(table.dataset_id if len(chunks) == 1 else f"{table.dataset_id} (batch {i + 1} of {len(chunks)})")
            self._apply(table, dataset, proposals)
        return result, failures

    def _call(self, table, dataset, names, with_dataset):
        allowed = names or ["none"]  # an enum cannot be empty; a table with no pending columns asks for none
        schema = self.schema(allowed, with_dataset)
        output = validate(self.model.complete("catalog_enrich", PROMPT, self.payload(table, dataset, names), schema), schema)
        # A repeated column keeps its first proposal; unrequested names are dropped.
        unique = {}
        for column in output["columns"]:
            if column["name"] in names:
                unique.setdefault(column["name"], column)
        output["columns"] = list(unique.values())
        return output

    def _apply(self, table, dataset, proposals):
        first = proposals[0]
        if first is not None:
            summary, grain_text = first["summary"].strip()[:1000], " ".join(first["grain"].split())[:300]
            if summary:
                dataset.summary = summary
            if grain_text:
                dataset.grain = grain_text
            for domain in first["domains"]:
                dataset.domains.setdefault(domain, "llm")
            dataset.review_state = "unreviewed"
        for proposal in (c for p in proposals if p is not None for c in p["columns"]):
            f = dataset.fields[proposal["name"]]
            description = " ".join(proposal["description"].split())[:MAX_DESCRIPTION]
            name = " ".join(proposal["business_name"].split())[:MAX_BUSINESS_NAME]
            if description and f.basis != "classifier":
                f.description = description
            if name and f.basis != "classifier":
                f.business_name = name
            f.review_state = "unreviewed"
            mapping, confidence = proposal["ontology_mapping"], proposal["confidence"]
            if (f.basis == "classifier" or mapping == "none" or not isinstance(confidence, (int, float))
                    or not MIN_MODEL_CONFIDENCE <= confidence <= 1 or mapping not in self.mappings):
                continue
            cid, pid = mapping.split(".", 1)
            if self.classifier.compatible(cid, pid, f.semantic_role, table.column(f.name)):
                f.ontology_class, f.ontology_property = cid, pid
                f.confidence, f.basis = round(min(float(confidence), MAX_MODEL_CONFIDENCE), 2), "llm"
                f.masked = f.masked or self.registry.get_property(cid, pid).pii
                if f.masked:
                    f.value_set = f.value_pattern = None


def model_input_size(enricher, harvest, classification):
    """Largest request in characters, to check the adapter's input bound before a paid run."""
    sizes = [0]
    for table in harvest.tables:
        dataset = classification.datasets[table.dataset_id]
        names = [c.name for c in table.columns][:enricher.columns_per_call]
        sizes.append(len(json.dumps(enricher.payload(table, dataset, names))) + len(json.dumps(enricher.schema(names, True))) + len(PROMPT))
    return max(sizes)
