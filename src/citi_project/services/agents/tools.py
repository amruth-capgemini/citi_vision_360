"""Canonical resolution and explicit specialist tool permissions, without I/O."""

from copy import deepcopy
from decimal import Decimal
import re

from .contracts import AgentError, SPECIALISTS, TOOL_SCHEMAS, validate
from ..structured_data.query_service import NAMESPACE


class EntityResolutionError(AgentError):
    def __init__(self, status, candidates=()):
        super().__init__("Vendor identity needs clarification")
        self.status = status
        self.candidates = list(candidates)


class CanonicalResolver:
    def __init__(self, structured):
        response = structured.list_vendor_contracts()
        if response["namespace"] != NAMESPACE:
            raise AgentError("Invalid canonical namespace")
        self.catalog = [{"vendor_id": r["Vendor_ID"], "vendor_name": r["Vendor_Name"]} for r in response["facts"]["contracts"]]

    def resolve(self, mention):
        value = mention.strip().casefold()
        exact = [r for r in self.catalog if value in (r["vendor_id"].casefold(), r["vendor_name"].casefold())]
        if len(exact) == 1:
            return exact[0]["vendor_id"]
        candidates = exact or [r for r in self.catalog if value and value in r["vendor_name"].casefold()]
        raise EntityResolutionError("clarification" if candidates else "not_found", candidates)

    def explicit(self, question):
        mentions = re.findall(r"\bV-\d+\b", question, re.I)
        mentions += [r["vendor_name"] for r in self.catalog if re.search(r"(?<!\w)" + re.escape(r["vendor_name"]) + r"(?!\w)", question, re.I)]
        return sorted({self.resolve(m) for m in mentions})


def unsupported_action(question):
    return bool(re.search(r"\b(delete|drop|truncate|ingest|commit|push|overwrite)\b|\b(update|write|modify|insert)\b.{0,50}\b(graph|database|neo4j|csv|file|schema|data)\b|\b(execute|eval)\b|\b(run|generate)\b.{0,30}\b(cypher|sql|python|shell)\b", question, re.I))


def _mentioned(value, question):
    return re.search(r"(?<!\w)" + re.escape(str(value)) + r"(?!\w)", question, re.I) is not None


class ApprovedTools:
    def __init__(self, decision):
        if decision.structured.namespace != NAMESPACE:
            raise AgentError("Tools require the canonical namespace")
        self.decision = decision
        self._tools = {name: getattr(decision, name) for names in SPECIALISTS.values() for name in names}

    def prepare(self, specialist, call, question, vendor_ids):
        name = call["name"]
        if name not in SPECIALISTS[specialist]:
            raise AgentError("Tool is not permitted for this specialist")
        args = deepcopy(validate(call["arguments"], TOOL_SCHEMAS[name]))
        organizations = set(re.findall(r"\bORG-\d{2}\b", question, re.I))
        if "organization_id" in args and organizations and organizations != {args.get("organization_id")}:
            raise AgentError("Explicit organization scope must be preserved")
        vendor = args.get("vendor_id")
        if vendor is not None and vendor not in vendor_ids:
            raise AgentError("Tool vendor was not deterministically resolved")
        if vendor_ids and vendor is None:
            raise AgentError("A vendor-scoped request cannot silently expand to a portfolio")
        for key in ("organization_id", "as_of_date"):
            if args.get(key) is not None and not _mentioned(args[key], question):
                raise AgentError("Tool scope was not supplied by the user")
        for key, default in (("days", 90), ("year", 2026)):
            if args.get(key) not in (None, default) and not _mentioned(args[key], question):
                raise AgentError("Tool parameter was not supplied by the user")
        if name == "run_workforce_scenario":
            if args["action"] != "baseline" and not re.search(r"\b(what.?if|hypothetical|scenario|simulate)\b|\bwhat (?:happens|happened|would happen) if\b", question, re.I):
                raise AgentError("Workforce changes require an explicit hypothetical scenario")
            for key in ("country", "market", "target_country", "worker_type", "target_worker_type"):
                value = args.get(key)
                if value and not re.search(re.escape(value) + r"s?\b", question, re.I):
                    raise AgentError("Scenario category was not supplied by the user")
            for identity in args.get("assignment_ids") or []:
                if not _mentioned(identity, question):
                    raise AgentError("Assignment ID was not supplied by the user")
            count, percentage = args.pop("assignment_count"), args.get("percentage")
            if count is not None:
                words = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
                if percentage is not None or args.get("assignment_ids") is not None or args["action"] == "baseline":
                    raise AgentError("Count scenario has conflicting inputs")
                if not (_mentioned(count, question) or (count < len(words) and _mentioned(words[count], question))):
                    raise AgentError("Assignment count was not supplied by the user")
                args["_requested_count"] = count
            elif args["action"] != "baseline":
                requested = re.findall(r"(\d+(?:\.\d+)?)\s*(?:%|percent\b)", question, re.I)
                if percentage is None or Decimal(str(percentage)) not in {Decimal(n) for n in requested}:
                    raise AgentError("Scenario percentage must be explicit")
            elif percentage not in (None, 0):
                raise AgentError("Baseline cannot change assignments")
        return {"name": name, "arguments": {k: v for k, v in args.items() if v is not None}, "specialist": specialist}

    @staticmethod
    def cost(call):
        return 2 if "_requested_count" in call["arguments"] else 1

    def execute(self, call):
        name, args = call["name"], deepcopy(call["arguments"])
        # No dynamic lookup is performed on a model-supplied object or service.
        if name not in SPECIALISTS[call["specialist"]]:
            raise AgentError("Tool permission denied")
        count = args.pop("_requested_count", None)
        if count is not None:
            # Probe a read-only overlay to obtain eligible IDs using existing
            # deterministic filtering/selection logic; never derive a percentage.
            preview = self._tools[name](**{**args, "percentage": 100})
            ids = preview["facts"]["affected_assignment_ids"]
            if count > len(ids):
                raise AgentError("Requested count exceeds the eligible representative assignments")
            args.update(assignment_ids=ids[:count], percentage=100)
        value = self._tools[name](**args)
        if value["namespace"] != NAMESPACE:
            raise AgentError("Tool response namespace mismatch")
        if count is not None:
            value["assumptions"].append({"requested_assignment_count": count,
                                         "selection": "First eligible Assignment_ID values; 100% of that explicitly selected cohort"})
        return {"tool": name, "specialist": call["specialist"], "arguments": args, "result": deepcopy(value)}
