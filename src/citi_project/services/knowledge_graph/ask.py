"""Deterministic, bounded business questions over the existing read-only service."""

from dataclasses import asdict, dataclass, field
import re

from .identity import canonical_identity
from .models import EntityRef


@dataclass
class AskRoute:
    intent: str | None = None
    arguments: dict = field(default_factory=dict)
    focus: str | None = None
    status: str = "answered"
    message: str = ""


@dataclass
class AskResponse:
    status: str
    namespace: str
    intent: str | None
    answer: str
    facts_used: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    limitations: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


_METHODS = {
    "vendor_contracts": "get_vendor_contracts",
    "contract_dependencies": "get_contract_dependencies",
    "contract_clauses": "get_contract_clauses",
    "contract_risk_sla": "get_contract_risk_and_sla",
    "evidence": "get_evidence_for_entity_or_relationship",
}
_CLASSES = {c.lower(): c for c in (
    "Vendor", "Contract", "StatementOfWork", "Service", "Application",
    "RenewalClause", "TerminationClause", "PaymentTermsClause", "ConfigurationItem",
    "BusinessProcess", "Product", "ServiceLevelAgreement", "PerformanceMeasurement",
    "RiskAssessment", "RiskIssue", "ContractDocument", "DependencyRegisterDocument",
    "SlaRiskPackDocument",
)}


def _selector(text):
    """An explicit class and identity, or a contract-scoped clause occurrence."""
    text = text.strip().rstrip("?.!")
    clause = re.fullmatch(r"(RenewalClause|TerminationClause|PaymentTermsClause) ([\w-]+) in (CTR-\d{3})", text, re.I)
    if clause:
        return EntityRef(_CLASSES[clause[1].lower()], contract_identity=clause[3].upper(), occurrence_id=clause[2])
    entity = re.fullmatch(r"([A-Za-z]+) ([\w-]+)", text)
    if entity and entity[1].lower() in _CLASSES and not entity[1].lower().endswith("clause"):
        identity = entity[2]
        if re.fullmatch(r"(?:V|CTR|SOW|SVC|APP|CI|PROC|PROD|SLA|MET|RA|ISSUE)-[\d-]+", identity, re.I):
            identity = identity.upper()
        return EntityRef(_CLASSES[entity[1].lower()], identity)
    raise ValueError("Use an explicit entity class and ID, or clause class, occurrence and contract ID.")


def route_question(question):
    """Pure routing. No graph access and no namespace or method supplied by text."""
    def clarify(message):
        return AskRoute(status="needs_clarification", message=message)
    if not isinstance(question, str) or not question.strip():
        return clarify("Please provide a business question and its identifier.")
    if len(question) > 1000:
        return AskRoute(status="unsupported", message="Questions are limited to 1,000 characters.")
    text = " ".join(question.split())
    if re.search(r"\b(evidence|provenance|source for)\b", text, re.I):
        match = re.fullmatch(r"(?:show |what is the )?(?:evidence|provenance|source) for (.+?)[?.!]?", text, re.I)
        if not match:
            return clarify("Use 'Show evidence for <Class> <ID>' or an explicit directed relationship.")
        selector = match[1]
        relationship = re.fullmatch(r"([A-Z_]+) from (.+) to (.+)", selector, re.I)
        try:
            arguments = ({"relationship_type": relationship[1].upper(),
                          "source": _selector(relationship[2]), "target": _selector(relationship[3])}
                         if relationship else {"entity": _selector(selector)})
        except ValueError as exc:
            return clarify(str(exc))
        return AskRoute("evidence", arguments)
    vendors = set(re.findall(r"(?<![\w-])V-\d{3}(?![\w-])", text.upper()))
    contracts = set(re.findall(r"(?<![\w-])CTR-\d{3}(?![\w-])", text.upper()))
    candidates = []
    if re.search(r"\b(dependencies|dependency|services?|applications?|portal|configuration items?|business processes?|products?)\b", text, re.I):
        candidates.append("contract_dependencies")
    if re.search(r"\b(clauses?|renewal|notice|termination|payment terms)\b", text, re.I):
        candidates.append("contract_clauses")
    if re.search(r"\b(risk|findings?|SLA|breaches?|performance)\b", text, re.I):
        candidates.append("contract_risk_sla")
    if re.search(r"\bcontracts?\b", text, re.I) and (vendors or re.search(r"\bvendor\b", text, re.I)):
        candidates.append("vendor_contracts")
    if not candidates:
        return AskRoute(status="unsupported", message="Supported questions cover vendor contracts, contract dependencies, clauses, risk/SLA, and explicit evidence lookups.")
    if len(candidates) != 1:
        return clarify("Please ask about one query type at a time.")
    intent = candidates[0]
    ids, other = (vendors, contracts) if intent == "vendor_contracts" else (contracts, vendors)
    if len(ids) != 1 or other:
        return clarify("Provide exactly one vendor ID (V-001)." if intent == "vendor_contracts" else "Provide exactly one contract ID (CTR-001).")
    focus = None
    if intent == "contract_clauses":
        occurrence = re.search(r"\bclause ([\w-]+) in\b", text, re.I)
        if occurrence:
            focus = "occurrence:" + occurrence[1]
        elif re.search(r"\brenewal\b", text, re.I):
            focus = "RenewalClause"
        elif re.search(r"\btermination\b", text, re.I):
            focus = "TerminationClause"
        elif re.search(r"\bpayment terms\b", text, re.I):
            focus = "PaymentTermsClause"
    if intent == "contract_risk_sla":
        has_risk = bool(re.search(r"\b(risk|findings?)\b", text, re.I))
        has_sla = bool(re.search(r"\b(SLA|breaches?|performance)\b", text, re.I))
        focus = None if has_risk and has_sla else "risk" if has_risk else "breaches" if re.search(r"\bbreaches?\b", text, re.I) else "sla"
    return AskRoute(intent, {"vendor_id" if intent == "vendor_contracts" else "contract_id": next(iter(ids))}, focus)


def _ref(node):
    return EntityRef(node["class_id"], node.get("identity"), node.get("contract_identity"), node.get("occurrence_id"))


def _name(node):
    return node.get("identity") or f"{node['contract_identity']} / {node['occurrence_id']}"


class KnowledgeGraphAskService:
    """No client access: execution is restricted to five public query methods."""

    def __init__(self, query_service, *, max_facts=20, max_evidence=100):
        if type(max_facts) is not int or not 1 <= max_facts <= 100:
            raise ValueError("max_facts must be between 1 and 100")
        if type(max_evidence) is not int or not 1 <= max_evidence <= 200:
            raise ValueError("max_evidence must be between 1 and 200")
        self.query_service = query_service
        self.max_facts, self.max_evidence = max_facts, max_evidence

    def ask(self, question):
        route = route_question(question)
        response = AskResponse(route.status, self.query_service.namespace, route.intent, route.message)
        if route.status != "answered":
            return response
        try:
            for value in route.arguments.values():
                if isinstance(value, EntityRef):
                    canonical_identity(self.query_service.registry, response.namespace, value)
            if route.intent == "evidence" and "relationship_type" in route.arguments:
                self.query_service.registry.validate_relation(route.arguments["relationship_type"], route.arguments["source"].class_id, route.arguments["target"].class_id)
        except ValueError:
            response.status, response.answer = "needs_clarification", "Provide valid entity IDs and a valid directed relationship selector."
            return response
        try:
            result = getattr(self.query_service, _METHODS[route.intent])(**route.arguments)
            self._build(response, route, result)
        except Exception:
            # Never echo backend exceptions, which can contain connection details.
            response.status, response.answer = "error", "The graph request could not be completed."
            response.facts_used, response.evidence = [], []
            self._limit(response, "query_error", "No complete answer is available; retry after checking the graph service.")
        return response

    @staticmethod
    def _limit(response, code, message):
        entry = {"code": code, "message": message}
        if entry not in response.limitations:
            response.limitations.append(entry)

    def _evidence(self, response, selector, cache):
        key = repr(selector)
        if key in cache:
            return cache[key]
        if len(cache) >= self.max_evidence:
            self._limit(response, "evidence_limit", "Some evidence lookups were omitted because the request limit was reached.")
            return None
        result = self.query_service.get_evidence_for_entity_or_relationship(**selector)
        evidence_id = "E" + str(len(response.evidence) + 1)
        cache[key] = evidence_id
        self._record_evidence(response, selector, result, evidence_id)
        return evidence_id

    def _record_evidence(self, response, selector, result, evidence_id):
        response.evidence.append({"evidence_id": evidence_id,
                                  "subject": {k: asdict(v) if isinstance(v, EntityRef) else v for k, v in selector.items()},
                                  "found": result["found"], "citations": result["citations"]["items"],
                                  "documents": result["documents"]["items"], "evidenced_by": result["evidenced_by"]["items"]})
        if not result["found"] or not result["citations"]["items"]:
            self._limit(response, "missing_provenance", "At least one selected fact has no available stored citation.")
        for name in ("citations", "documents", "evidenced_by"):
            if result[name]["truncated"]:
                self._limit(response, "truncated", "Evidence results are truncated.")
        documents = {_ref(d) for d in result["documents"]["items"]}
        for citation in result["citations"]["items"]:
            if citation.get("document") and EntityRef(**citation["document"]) not in documents:
                self._limit(response, "unresolved_document", "A cited document was not resolved in the returned evidence.")
            if citation.get("review_state") != "approved" or citation.get("verification_state") != "verified":
                self._limit(response, "unverified_provenance", "Stored provenance is not established as approved and verified source evidence.")
            if "synthetic" in str(citation.get("source_ref", "")).lower() or "synthetic" in str(citation.get("evidence_ref", "")).lower():
                self._limit(response, "synthetic_provenance", "Citations describe synthetic fixture assertions, not verified source documents.")

    def _build(self, response, route, result):
        if route.intent == "evidence":
            self._record_evidence(response, route.arguments, result, "E1")
            response.answer = (f"Found {len(result['citations']['items'])} stored citation(s) and {len(result['documents']['items'])} resolved document(s). [E1]"
                               if result["found"] else "The requested entity or relationship was not found in this namespace.")
            if not result["found"]:
                self._limit(response, "not_found", "Absence from this namespace does not establish absence elsewhere.")
            return
        if result["root"] is None:
            response.answer = "The requested entity was not found in this namespace."
            self._limit(response, "not_found", "Absence from this namespace does not establish absence elsewhere.")
            return
        selected = []
        for collection, group in result.items():
            if not isinstance(group, dict) or "items" not in group:
                continue
            if group["truncated"]:
                self._limit(response, "truncated", f"The {collection} collection is truncated; totals may be incomplete.")
            for path in group["items"]:
                end = path["nodes"][-1]
                if route.intent == "contract_clauses" and route.focus:
                    if route.focus.startswith("occurrence:") and end["occurrence_id"] != route.focus.partition(":")[2]:
                        continue
                    if not route.focus.startswith("occurrence:") and end["class_id"] != route.focus:
                        continue
                if route.intent == "contract_risk_sla" and route.focus:
                    if route.focus == "risk" and collection in ("slas", "measurements"):
                        continue
                    if route.focus in ("sla", "breaches") and collection not in ("slas", "measurements"):
                        continue
                    if route.focus == "breaches" and (collection != "measurements" or end["properties"].get("breach") is not True):
                        continue
                selected.append((collection, path))
        if len(selected) > self.max_facts:
            self._limit(response, "fact_limit", "Some returned paths were omitted from the answer because the fact limit was reached.")
        cache, sentences = {}, []
        for collection, path in selected[:self.max_facts]:
            fact_id = "F" + str(len(response.facts_used) + 1)
            evidence_ids = []
            by_key = {n["key"]: n for n in path["nodes"]}
            selectors = [{"entity": _ref(n)} for n in path["nodes"]]
            selectors += [{"relationship_type": edge["type"], "source": _ref(by_key[edge["source"]]), "target": _ref(by_key[edge["target"]])} for edge in path["relationships"]]
            for selector in selectors:
                evidence_id = self._evidence(response, selector, cache)
                if evidence_id and evidence_id not in evidence_ids:
                    evidence_ids.append(evidence_id)
            response.facts_used.append({"fact_id": fact_id, "collection": collection, "path": path, "evidence_ids": evidence_ids})
            sentences.append(self._sentence(route.intent, collection, path) + f" [{fact_id}]")
        response.answer = " ".join(sentences) or "No matching facts were returned for this question."
        if not selected:
            self._limit(response, "missing_data", "No matching returned facts does not establish that no such business facts exist.")
        self._limit(response, "read_consistency", "Facts and evidence are separate bounded reads without a shared snapshot guarantee.")

    @staticmethod
    def _sentence(intent, collection, path):
        end, root = path["nodes"][-1], path["nodes"][0]
        name, props = _name(end), end["properties"]
        if intent == "vendor_contracts":
            return f"{_name(root)} is party to {name}."
        if intent == "contract_clauses":
            details = ", ".join(f"{key}={value}" for key, value in sorted(props.items()))
            return f"{name} ({end['class_id']}, revision {end['revision']}): {details}."
        if collection == "measurements":
            return f"{name}: actual {props.get('actual', 'missing')}%, target {props.get('target', 'missing')}%, breach={props.get('breach', 'missing')}, period {props.get('period', 'missing')}."
        if end["class_id"] == "RiskAssessment":
            return f"{name}: assessment status {props.get('status', 'missing')}, risk tier {props.get('risk_tier', 'not recorded')}."
        if end["class_id"] == "ServiceLevelAgreement":
            return f"{name}: target {props.get('target_percent', 'missing')}%."
        # Preserve every edge direction, including inverse risk traversals and portal use.
        parts = [_name(root)]
        for index, edge in enumerate(path["relationships"]):
            outgoing = edge["source"] == path["nodes"][index]["key"]
            parts.append((f" -{edge['type']}-> " if outgoing else f" <-{edge['type']}- ") + _name(path["nodes"][index + 1]))
        suffix = f"; {props.get('severity', 'severity missing')}, {props.get('status', 'status missing')}: {props.get('finding', 'finding missing')}" if end["class_id"] == "RiskIssue" else ""
        return "".join(parts) + suffix + "."
