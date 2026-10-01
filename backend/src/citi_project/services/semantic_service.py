"""Deterministic structured + graph context; no recommendations or Cypher."""

from copy import deepcopy
from datetime import date

from .knowledge_graph.models import EntityRef
from .structured_data.query_service import NAMESPACE, StructuredDataError, decimal_value


def calculate_sla_breach(actual, target, direction):
    """Unknown values/directions remain unknown, never an implicit pass."""
    if actual is None or target is None or direction not in ("higher_is_better", "lower_is_better"):
        return None
    try:
        actual, target = decimal_value(actual), decimal_value(target)
    except StructuredDataError:
        return None
    return actual < target if direction == "higher_is_better" else actual > target


def _ref(node):
    return EntityRef(node["class_id"], node.get("identity"), node.get("contract_identity"), node.get("occurrence_id"))


def _identity(node):
    return node.get("identity") or {"contract_id": node.get("contract_identity"), "class_id": node["class_id"], "occurrence_id": node.get("occurrence_id")}


class VendorSemanticService:
    """Context composed through the existing public graph read methods only.

    A graph instance is injected, just like the existing Ask service. Calls and
    evidence lookups are cached only within a request. Failures preserve explicit
    missing-data limitations and never imply a negative business finding.
    """

    def __init__(self, structured_service, graph_query_service=None, *, max_evidence=80):
        if structured_service.namespace != NAMESPACE or (graph_query_service is not None and graph_query_service.namespace != NAMESPACE):
            raise StructuredDataError("Semantic sources must use the canonical synthetic namespace")
        if type(max_evidence) is not int or not 1 <= max_evidence <= 200:
            raise StructuredDataError("max_evidence must be between 1 and 200")
        self.structured = structured_service
        self.graph = graph_query_service
        self.max_evidence = max_evidence

    @staticmethod
    def _note(result, code, message):
        item = {"code": code, "message": message}
        if item not in result["limitations"]:
            result["limitations"].append(item)

    @staticmethod
    def _merge(result, component):
        for field in ("evidence", "limitations"):
            for item in component[field]:
                if item not in result[field]:
                    result[field].append(item)

    def _base(self, vendor_id, question_type):
        contract = self.structured.get_vendor_contract(vendor_id)
        result = {"namespace": NAMESPACE, "vendor_id": vendor_id, "contract_id": contract["contract_id"],
                  "question_type": question_type, "facts": {}, "evidence": [], "limitations": []}
        self._merge(result, contract)
        master = contract["facts"]["contract"]
        if master:
            result["facts"]["vendor_name"] = master["Vendor_Name"]
            result["facts"]["as_of_date"] = master["As_Of_Date"]
        return result, master

    def _lookup_evidence(self, selector, subject, result, cache):
        key = repr(selector)
        if key in cache:
            return
        if len(cache) >= self.max_evidence:
            self._note(result, "evidence_limit", "Additional document evidence was omitted at the request limit.")
            return
        cache[key] = True
        try:
            evidence = self.graph.get_evidence_for_entity_or_relationship(**selector)
        except Exception:
            self._note(result, "evidence_unavailable", "A graph evidence lookup failed; backend details are suppressed.")
            return
        if evidence.get("namespace") != NAMESPACE:
            self._note(result, "evidence_scope_mismatch", "Evidence returned outside the canonical namespace was excluded.")
            return
        result["evidence"].append({"source_type": "neo4j_evidence", "subject": subject, "found": evidence["found"],
                                   "citations": deepcopy(evidence["citations"]), "documents": deepcopy(evidence["documents"]),
                                   "evidenced_by": deepcopy(evidence["evidenced_by"])})
        if not evidence["found"] or not evidence["citations"]["items"]:
            self._note(result, "missing_provenance", "Stored citation evidence is unavailable for at least one graph fact.")
        for name in ("citations", "documents", "evidenced_by"):
            if evidence[name]["truncated"]:
                self._note(result, "truncated_evidence", "Graph evidence collections are incomplete because of their limit.")
        doc_refs = {_ref(d) for d in evidence["documents"]["items"]}
        for citation in evidence["citations"]["items"]:
            document = citation.get("document")
            if document:
                result["evidence"].append({"source_type": "document", "subject": subject,
                    "document_id": document.get("identity"), **{k: deepcopy(v) for k, v in citation.items() if k != "document"}})
                if EntityRef(**document) not in doc_refs:
                    self._note(result, "unresolved_document", "A citation's document was not resolved in returned graph evidence.")
            if citation.get("review_state") != "approved" or citation.get("verification_state") != "verified":
                self._note(result, "unverified_provenance", "Stored citations are not established as approved, verified source evidence.")
            if any("synthetic" in str(citation.get(k, "")).lower() for k in ("source_ref", "evidence_ref")):
                self._note(result, "synthetic_provenance", "Document citations describe synthetic source assertions.")

    def _graph_result(self, method, identifier, result, request):
        key = (method, identifier)
        if key in request["queries"]:
            return request["queries"][key]
        if self.graph is None:
            self._note(result, "graph_unavailable", "No graph query service was supplied; graph context is unavailable.")
            request["queries"][key] = None
            return None
        # Caller methods use only fixed literal method names.
        try:
            value = getattr(self.graph, method)(identifier)
        except Exception:
            self._note(result, "graph_unavailable", "Graph read failed; missing graph results are not a negative business finding.")
            request["queries"][key] = None
            return None
        if value.get("namespace") != NAMESPACE or (value.get("root") and value["root"].get("identity") != identifier):
            self._note(result, "graph_identity_mismatch", "Graph response does not match the requested canonical scope.")
            request["queries"][key] = None
            return None
        request["queries"][key] = value
        if value["root"] is None:
            self._note(result, "graph_root_missing", "Canonical entity was not found by the graph query.")
            return value
        self._note(result, "read_consistency", "Structured data is a snapshot; graph facts and evidence are separate reads without a shared snapshot guarantee.")
        root = value["root"]
        self._lookup_evidence({"entity": _ref(root)}, {"class_id": root["class_id"], "identity": _identity(root)}, result, request["evidence"])
        for name, collection in value.items():
            if not isinstance(collection, dict) or "items" not in collection:
                continue
            if collection["truncated"]:
                self._note(result, "truncated_graph", f"The {name} graph collection is truncated; missing paths cannot be ruled out.")
            for path in collection["items"]:
                by_key = {n["key"]: n for n in path["nodes"]}
                for node in path["nodes"]:
                    self._lookup_evidence({"entity": _ref(node)}, {"class_id": node["class_id"], "identity": _identity(node)}, result, request["evidence"])
                for edge in path["relationships"]:
                    source, target = by_key[edge["source"]], by_key[edge["target"]]
                    item = {"source_type": "neo4j", "key": edge["key"], "relationship": edge["type"],
                            "source": _identity(source), "target": _identity(target), "revision": edge.get("revision"), "namespace": NAMESPACE}
                    if item not in result["evidence"]:
                        result["evidence"].append(item)
                    self._lookup_evidence({"relationship_type": edge["type"], "source": _ref(source), "target": _ref(target)}, item, result, request["evidence"])
        return value

    @staticmethod
    def _paths(value, collection):
        return value.get(collection, {}).get("items", []) if value else []

    def _dependencies(self, vendor_id, master, result, request):
        links = self._graph_result("get_vendor_contracts", vendor_id, result, request)
        actual_contracts = {p["nodes"][-1]["identity"] for p in self._paths(links, "contracts")}
        if links and links["root"] and actual_contracts != {master["Contract_ID"]}:
            self._note(result, "contract_link_mismatch", "Graph vendor-contract links differ from the canonical master.")
        dependencies = self._graph_result("get_contract_dependencies", master["Contract_ID"], result, request)
        applications = {}
        for path in self._paths(dependencies, "applications"):
            node = path["nodes"][-1]
            edge = path["relationships"][-1]
            service = next(n for n in path["nodes"] if n["class_id"] == "Service")
            signature = (service["identity"], node["identity"], edge["type"])
            applications[signature] = {"service_id": service["identity"], "application_id": node["identity"],
                                        "relationship": edge["type"], "properties": deepcopy(node["properties"])}
        bridge = self.structured.get_vendor_application_bridge(vendor_id)
        self._merge(result, bridge)
        expected = {(r["Service_ID"], r["Application_ID"], r["Relationship_Type"]) for r in bridge["facts"]["applications"]}
        if dependencies and dependencies["root"] and set(applications) != expected:
            self._note(result, "application_bridge_mismatch", "Graph applications and the structured application bridge differ; graph paths are returned explicitly.")
        facts = {"applications": list(applications.values()), "services": sorted({p["nodes"][-1]["identity"] for p in self._paths(dependencies, "services")}),
                 "statements_of_work": sorted({p["nodes"][-1]["identity"] for p in self._paths(dependencies, "statements_of_work")}),
                 "dependency_paths": deepcopy({k: v for k, v in (dependencies or {}).items() if isinstance(v, dict) and "items" in v}),
                 "graph_dependencies_available": bool(dependencies and dependencies["root"])}
        if facts["graph_dependencies_available"] and facts["services"] != [master["Service_ID"]]:
            self._note(result, "service_link_mismatch", "Graph funded services differ from the canonical master.")
        return facts

    def _risk_sla(self, master, result, request):
        graph = self._graph_result("get_contract_risk_and_sla", master["Contract_ID"], result, request)
        assessments = {}
        for name in ("vendor_assessments", "service_assessments"):
            for path in self._paths(graph, name):
                node = path["nodes"][-1]
                props = deepcopy(node["properties"])
                if props.get("status") == "Missing":
                    props["risk_tier"] = None
                    self._note(result, "risk_missing", "Risk assessment evidence is unavailable; a complete risk assessment cannot be made and no tier is inferred.")
                elif props.get("status") == "Stale":
                    self._note(result, "risk_stale", "Risk assessment is stale; its date and tier are last-known evidence, not a current assessment.")
                assessments[node["identity"]] = {"assessment_id": node["identity"], **props}
        if not assessments:
            self._note(result, "risk_unavailable", "No assessment was returned; status is unknown, not an inferred Missing assessment.")
        elif master["Risk_Assessment_ID"] not in assessments:
            self._note(result, "assessment_identity_mismatch", "Returned assessments differ from the canonical assessment ID.")
        measurements = {}
        for path in self._paths(graph, "measurements"):
            node = path["nodes"][-1]
            sla = next(n for n in path["nodes"] if n["class_id"] == "ServiceLevelAgreement")
            p, sp = node["properties"], sla["properties"]
            # The existing ontology explicitly defines percentage attainment and
            # breach as actual below target. No metric-name guessing is used.
            direction = "higher_is_better" if p.get("unit") == "percent" else None
            target = sp.get("target_percent")
            breach = calculate_sla_breach(p.get("actual"), target, direction)
            try:
                target_matches = p.get("target") is not None and target is not None and decimal_value(p["target"]) == decimal_value(target)
            except StructuredDataError:
                target_matches = False
            if not target_matches:
                breach = None
                self._note(result, "sla_target_mismatch", "Measurement and contractual targets are missing or differ; breach is unknown pending target reconciliation.")
            if breach is None:
                self._note(result, "sla_unknown", "SLA breach cannot be determined for an unsupported direction/unit or missing numeric evidence.")
            elif p.get("breach") is not None and p["breach"] != breach:
                self._note(result, "sla_flag_mismatch", "Stored breach flag differs from the deterministic numeric calculation.")
            measurements[node["identity"]] = {"measurement_id": node["identity"], "sla_id": sla["identity"], "period": p.get("period"),
                "metric": p.get("metric_name"), "unit": p.get("unit"), "actual": p.get("actual"), "target": target,
                "direction": direction, "breach": breach, "stored_breach": p.get("breach"),
                "rule_source": "ontology/risk.yaml PerformanceMeasurement attainment semantics"}
        values = list(measurements.values())
        truncated = bool(graph and graph.get("measurements", {}).get("truncated"))
        has_breach = True if any(m["breach"] is True for m in values) else False if values and not truncated and all(m["breach"] is False for m in values) else None
        if not values:
            self._note(result, "sla_unavailable", "No SLA measurements were returned; absence is not a passing SLA result.")
        statuses = sorted({p.get("status", "Unknown") for p in assessments.values()})
        return {"risk_assessments": list(assessments.values()), "risk_status": statuses[0] if len(statuses) == 1 else None,
                "sla_measurements": values, "has_sla_breach": has_breach,
                "risk_issue_paths": deepcopy({k: v for k, v in (graph or {}).items() if "issues" in k}),
                "risk_status_as_of": master["As_Of_Date"]}

    @staticmethod
    def _request():
        return {"queries": {}, "evidence": {}}

    def get_vendor_financial_context(self, vendor_id, year=2026):
        result = self.structured.get_budget_forecast_actual(vendor_id, year)
        contract = self.structured.get_vendor_contract(vendor_id)
        self._merge(result, contract)
        result["question_type"] = "financial_context"
        return result

    def get_vendor_workforce_context(self, vendor_id, group_by=None):
        result = self.structured.get_vendor_workforce(vendor_id)
        self._merge(result, self.structured.get_vendor_contract(vendor_id))
        result["question_type"] = "workforce_context"
        if group_by is not None:
            grouped = self.structured.get_workforce_groups(group_by, vendor_id)
            result["facts"].update(grouped["facts"])
            self._merge(result, grouped)
        return result

    def get_vendor_dependency_context(self, vendor_id):
        result, master = self._base(vendor_id, "dependency_context")
        if master:
            result["facts"].update(self._dependencies(vendor_id, master, result, self._request()))
        return result

    def get_vendor_dependency_workforce_context(self, vendor_id):
        result = self.get_vendor_dependency_context(vendor_id)
        workforce = self.get_vendor_workforce_context(vendor_id)
        result["facts"].update(workforce["facts"])
        self._merge(result, workforce)
        result["question_type"] = "dependency_workforce_context"
        return result

    def get_vendor_risk_sla_context(self, vendor_id):
        result, master = self._base(vendor_id, "risk_sla_context")
        if master:
            result["facts"].update(self._risk_sla(master, result, self._request()))
        return result

    def get_contract_expiry_context(self, days=90, as_of_date=None):
        result = self.structured.get_expiring_contracts(days, as_of_date)
        result["question_type"] = "contract_expiry_context"
        return result

    def get_vendor_renewal_context(self, vendor_id, *, as_of_date=None, year=2026):
        result, master = self._base(vendor_id, "renewal_context")
        if master is None:
            return result
        if as_of_date is None:
            as_of = self.structured.as_of_date
        else:
            # Reuse the public structured API's date validation.
            checked = self.structured.get_expiring_contracts(0, as_of_date)
            as_of = date.fromisoformat(checked["facts"]["as_of_date"])
        result["facts"].update({"as_of_date": as_of.isoformat(), "contract_start_date": master["Contract_Start_Date"],
            "contract_end_date": master["Contract_End_Date"], "days_to_expiry": (date.fromisoformat(master["Contract_End_Date"]) - as_of).days})
        for component in (self.get_vendor_financial_context(vendor_id, year), self.structured.get_vendor_workforce_count(vendor_id)):
            result["facts"].update(component["facts"])
            self._merge(result, component)
        request = self._request()
        result["facts"].update(self._dependencies(vendor_id, master, result, request))
        result["facts"].update(self._risk_sla(master, result, request))
        clauses = self._graph_result("get_contract_clauses", master["Contract_ID"], result, request)
        result["facts"]["clause_paths"] = deepcopy(clauses.get("clauses")) if clauses else None
        if as_of.isoformat() != master["As_Of_Date"]:
            self._note(result, "snapshot_date", "Expiry uses the requested date; financial, workforce and risk evidence retain their original snapshot periods.")
        return result
