"""Deterministic review contexts and immutable workforce overlays; no decisions."""

from copy import deepcopy
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_UP
from itertools import combinations

from .structured_data.query_service import NAMESPACE, StructuredDataError, decimal_value, decimal_string, variance


@dataclass(frozen=True)
class DecisionPolicy:
    """Optional caller-supplied review thresholds, not renewal decision rules."""

    high_forecast_variance_pct: str | None = None
    high_dependency_count: int | None = None

    def __post_init__(self):
        if self.high_forecast_variance_pct is not None:
            value = decimal_value(self.high_forecast_variance_pct)
            if value < 0:
                raise StructuredDataError("Variance threshold must be nonnegative")
            object.__setattr__(self, "high_forecast_variance_pct", str(value))
        if self.high_dependency_count is not None and (type(self.high_dependency_count) is not int or self.high_dependency_count < 1):
            raise StructuredDataError("Dependency threshold must be a positive integer")


class DecisionIntelligenceService:
    """Composes public services only. Every result is detached from source data."""

    def __init__(self, semantic_service, *, policy=None, max_vendors=100):
        if semantic_service.structured.namespace != NAMESPACE or (semantic_service.graph is not None and semantic_service.graph.namespace != NAMESPACE):
            raise StructuredDataError("Decision sources must use the canonical namespace")
        if type(max_vendors) is not int or not 1 <= max_vendors <= 100:
            raise StructuredDataError("max_vendors must be between 1 and 100")
        if policy is not None and not isinstance(policy, DecisionPolicy):
            raise StructuredDataError("Expected a DecisionPolicy")
        self.semantic = semantic_service
        self.structured = semantic_service.structured
        self.policy = policy or DecisionPolicy()
        self.max_vendors = max_vendors

    @staticmethod
    def _result(capability, **scope):
        return {"namespace": NAMESPACE, "capability": capability, "scope": scope, "facts": {},
                "calculations": [], "flags": [], "evidence": [], "assumptions": [], "limitations": []}

    @staticmethod
    def _merge(result, source):
        if source.get("namespace") != NAMESPACE:
            raise StructuredDataError("Response namespace mismatch")
        for field in ("evidence", "limitations"):
            for item in source[field]:
                if item not in result[field]:
                    result[field].append(deepcopy(item))

    @staticmethod
    def _note(result, code, message):
        item = {"code": code, "message": message}
        if item not in result["limitations"]:
            result["limitations"].append(item)

    def _portfolio(self, result, organization_id=None):
        source = self.structured.list_vendor_contracts(organization_id)
        self._merge(result, source)
        rows = source["facts"]["contracts"]
        if len(rows) > self.max_vendors:
            raise StructuredDataError("Portfolio exceeds configured vendor bound")
        return rows

    @staticmethod
    def _evidence_status(context):
        codes = {n["code"] for n in context["limitations"]}
        gaps = sorted(c for c in codes if any(word in c for word in ("missing", "unavailable", "truncated", "mismatch", "unresolved", "limit", "unknown", "not_found")))
        return {"retrieval_status": "incomplete" if gaps else "no_reported_gaps",
                "gap_codes": gaps, "business_verified": False,
                "source_status": "synthetic POC; retrieval completeness is not evidence approval"}

    @staticmethod
    def _dependency_facts(facts, limitations):
        applications = facts.get("applications", [])
        available = facts.get("graph_dependencies_available", False)
        codes = {n["code"] for n in limitations}
        complete = available and not codes.intersection({"truncated_graph", "application_bridge_mismatch", "service_link_mismatch", "graph_identity_mismatch", "contract_link_mismatch"})
        return {"applications": deepcopy(applications), "services": deepcopy(facts.get("services", [])),
                "application_count": len({a["application_id"] for a in applications}) if available else None,
                "supported_application_count": len({a["application_id"] for a in applications if a["relationship"] == "SUPPORTS"}) if available else None,
                "used_portal_count": len({a["application_id"] for a in applications if a["relationship"] == "USES_PORTAL"}) if available else None,
                "service_count": len(set(facts.get("services", []))) if available else None,
                "count_basis": "returned graph entities; lower bounds when incomplete", "complete": complete,
                "paths": deepcopy(facts.get("dependency_paths", {}))}

    @staticmethod
    def _risk_facts(facts, limitations):
        issues = {}
        for collection in facts.get("risk_issue_paths", {}).values():
            for path in collection.get("items", []):
                for node in path["nodes"]:
                    if node["class_id"] == "RiskIssue":
                        issues[node["identity"]] = {"issue_id": node["identity"], **deepcopy(node["properties"])}
        available = "risk_issue_paths" in facts and bool(facts["risk_issue_paths"])
        return {"status": facts.get("risk_status"), "status_as_of": facts.get("risk_status_as_of"),
                "assessments": deepcopy(facts.get("risk_assessments", [])), "issues": list(issues.values()),
                "open_issue_count": sum(i.get("status") == "Open" for i in issues.values()) if available else None,
                "issue_count_basis": "distinct returned issue IDs with source status Open",
                "issues_complete": available and not any(n["code"] == "truncated_graph" for n in limitations)}

    def get_vendor_360(self, vendor_id, *, as_of_date=None):
        result = self._result("vendor_360", vendor_id=vendor_id)
        master = self.structured.get_vendor_contract(vendor_id)
        self._merge(result, master)
        row = master["facts"]["contract"]
        if row is None:
            return result
        context = self.semantic.get_vendor_renewal_context(vendor_id, as_of_date=as_of_date)
        workforce = self.semantic.get_vendor_workforce_context(vendor_id)
        self._merge(result, context)
        self._merge(result, workforce)
        facts = context["facts"]
        financial = self.semantic.get_vendor_financial_context(vendor_id)
        self._merge(result, financial)
        result["scope"].update(contract_id=row["Contract_ID"], as_of_date=facts["as_of_date"])
        result["facts"] = {
            "identity": {k: row[k] for k in ("Vendor_ID", "Vendor_Name", "Contract_ID", "Organization_ID", "Product_ID", "BCID", "Tech_ID", "VRM_ID")},
            "commercial": {"contract": row, "days_to_expiry": facts["days_to_expiry"], "service_id": row["Service_ID"],
                           "sow_id": row["SOW_ID"], "clauses": facts.get("clause_paths")},
            "financial": financial["facts"], "workforce": workforce["facts"],
            "dependencies": self._dependency_facts(facts, context["limitations"]),
            "sla": {"has_breach": facts.get("has_sla_breach"), "measurements": facts.get("sla_measurements", []),
                    "breached_measurement_count": sum(m["breach"] is True for m in facts["sla_measurements"]) if facts.get("sla_measurements") else None,
                    "count_basis": "returned measurements only"},
            "risk": self._risk_facts(facts, context["limitations"]),
            "evidence_completeness": self._evidence_status(context),
        }
        result["assumptions"] = ["Expiry defaults to the structured snapshot date.", "No renewal recommendation is calculated."]
        return deepcopy(result)

    def _renewal(self, context, days):
        result = deepcopy(context)
        result["capability"] = "renewal"
        result["scope"]["window_days"] = days
        result["assumptions"].append({"review_policy": asdict(self.policy), "ordering": "expiry date, then contract ID; no weighted score"})
        if not result["facts"]:
            return result
        f = result["facts"]
        def flag(code, observed, rule):
            result["flags"].append({"code": code, "vendor_id": result["scope"]["vendor_id"], "observed": observed, "rule": rule})
        remaining = f["commercial"]["days_to_expiry"]
        if 0 <= remaining <= days:
            flag("EXPIRING_SOON", remaining, f"0 <= days_to_expiry <= {days}")
        pct = f["financial"].get("forecast_variance_pct")
        threshold = self.policy.high_forecast_variance_pct
        if threshold is not None and pct is not None and decimal_value(pct) > decimal_value(threshold):
            flag("HIGH_FORECAST_VARIANCE", pct, f"forecast variance percent > {threshold}; caller-supplied review threshold")
        count = f["dependencies"]["application_count"]
        if self.policy.high_dependency_count is not None and count is not None and count >= self.policy.high_dependency_count:
            flag("HIGH_DEPENDENCY", count, f"returned distinct application count >= {self.policy.high_dependency_count}; caller-supplied review threshold")
        if f["sla"]["has_breach"] is True:
            flag("SLA_BREACH", True, "At least one returned measurement breaches its contractual target")
        for status, code in (("Stale", "STALE_RISK"), ("Missing", "MISSING_RISK")):
            if any(a.get("status") == status for a in f["risk"]["assessments"]):
                flag(code, status, "Preserved source assessment status")
        if f["evidence_completeness"]["retrieval_status"] == "incomplete":
            flag("INCOMPLETE_EVIDENCE", f["evidence_completeness"]["gap_codes"], "One or more source retrieval/data gaps are reported")
        return result

    def get_renewal_context(self, vendor_id, *, days=90, as_of_date=None):
        # Reuse existing date/window validation even when the vendor is absent.
        self.structured.get_expiring_contracts(days, as_of_date)
        return self._renewal(self.get_vendor_360(vendor_id, as_of_date=as_of_date), days)

    def get_renewal_priorities(self, days=90, as_of_date=None):
        source = self.semantic.get_contract_expiry_context(days, as_of_date)
        result = self._result("renewal", days=days, as_of_date=source["facts"]["as_of_date"])
        self._merge(result, source)
        rows = source["facts"]["contracts"]
        if len(rows) > self.max_vendors:
            raise StructuredDataError("Portfolio exceeds configured vendor bound")
        contexts = [self.get_renewal_context(r["Vendor_ID"], days=days, as_of_date=as_of_date) for r in rows]
        for context in contexts:
            self._merge(result, context)
            result["flags"].extend(context["flags"])
        result["facts"] = {"contracts": [{"scope": c["scope"], "facts": c["facts"], "flags": c["flags"], "limitations": c["limitations"]} for c in contexts]}
        result["assumptions"] = [{"review_policy": asdict(self.policy)}, "Priority order is expiry date then contract ID; flags are review context, not renewal decisions."]
        return result

    def get_vendor_dependency_risk(self, vendor_id):
        result = self.get_vendor_360(vendor_id)
        result["capability"] = "risk"
        if result["facts"]:
            result["facts"] = {k: result["facts"][k] for k in ("identity", "dependencies", "workforce", "sla", "risk", "evidence_completeness")}
        return result

    def get_vendor_rationalization_opportunities(self, *, organization_id=None):
        result = self._result("rationalization", organization_id=organization_id)
        rows = self._portfolio(result, organization_id)
        profiles = {}
        for row in rows:
            vendor = row["Vendor_ID"]
            bridge = self.structured.get_vendor_application_bridge(vendor)
            financial = self.semantic.get_vendor_financial_context(vendor)
            workforce = self.structured.get_vendor_workforce_count(vendor)
            for source in (bridge, financial, workforce):
                self._merge(result, source)
            profiles[vendor] = {"identity": row, "applications": bridge["facts"]["applications"],
                                "financial": financial["facts"], "workforce": workforce["facts"]}
        pairs = []
        for a, b in combinations(rows, 2):
            va, vb = a["Vendor_ID"], b["Vendor_ID"]
            pa, pb = profiles[va], profiles[vb]
            shared = sorted({r["Application_ID"] for r in pa["applications"]} & {r["Application_ID"] for r in pb["applications"]})
            product = a["Product_ID"] == b["Product_ID"]
            services = [a["Service_ID"]] if a["Service_ID"] == b["Service_ID"] else []
            if not (product or shared or services):
                continue
            pairs.append({"vendor_a": va, "vendor_b": vb, "classification": "potential_overlap_candidate",
                          "overlap": {"product": product, "organization": a["Organization_ID"] == b["Organization_ID"], "shared_applications": shared, "shared_services": services,
                                      "application_relationships": {v: [r for r in profiles[v]["applications"] if r["Application_ID"] in shared] for v in (va, vb)}},
                          "financial_context": {va: pa["financial"], vb: pb["financial"]},
                          "dependency_context": {va: pa["workforce"], vb: pb["workforce"]},
                          "contract_timing": {va: a["Contract_End_Date"], vb: b["Contract_End_Date"]}})
        result["facts"] = {"candidate_count": len(pairs), "candidates": pairs, "vendors": profiles}
        result["assumptions"] = ["Candidate iff exact financial Product_ID matches, an application ID overlaps, or exact Service_ID matches; organization alone is not a candidate rule.", "Application overlap uses the validated canonical bridge snapshot, not a new live graph query."]
        self._note(result, "overlap_not_substitutability", "Shared products/apps do not establish interchangeable services, consolidation suitability or savings. Pair financial values repeat vendor context and must not be summed across pairs.")
        self._note(result, "unsupported_similarity", "No product/service similarity taxonomy or capability equivalence is inferred. USES_PORTAL remains distinct from SUPPORTS.")
        return result

    def get_spend_forecast_analysis(self, vendor_id, year=2026):
        result = self._result("spend_forecast", vendor_id=vendor_id, year=year)
        financial = self.semantic.get_vendor_financial_context(vendor_id, year)
        master = self.structured.get_vendor_contract(vendor_id)
        rows = self.structured.get_vendor_financials(vendor_id)
        for source in (financial, master, rows):
            self._merge(result, source)
        facts = deepcopy(financial["facts"])
        row = master["facts"]["contract"]
        scenarios = {r["Scenario"]: r for r in rows["facts"]["records"] if r["Year"] == str(year)}
        forecast = scenarios.get("Forecast")
        facts["forecast_ytd"] = forecast["YTD_Amount"] if forecast else None
        facts["actual_vs_forecast_ytd"] = variance(decimal_value(facts["actual_ytd"]), decimal_value(facts["forecast_ytd"])) if facts["actual_ytd"] is not None and forecast else {"amount": None, "percent": None}
        facts["allocation"] = {k: row[k] for k in ("Vendor_ID", "Contract_ID", "Organization_ID", "Product_ID", "BCID", "Tech_ID")} if row else None
        facts["contract_end_date"] = row["Contract_End_Date"] if row else None
        facts["monthly_scenarios"] = {scenario: {month: r[month + "_USD"] for month in "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()} for scenario, r in scenarios.items()}
        result["facts"] = facts
        result["calculations"] = [{"name": "forecast_variance", "formula": "full_year_forecast - full_year_budget", "amount": facts["forecast_variance_amount"], "percent": facts["forecast_variance_pct"]},
                                  {"name": "actual_variance", "formula": "actual_jan_aug - budget_jan_aug", "amount": facts["actual_ytd_variance_amount"], "percent": facts["actual_ytd_variance_pct"]},
                                  {"name": "actual_vs_forecast", "formula": "actual_jan_aug - forecast_jan_aug", **facts["actual_vs_forecast_ytd"]}]
        self._note(result, "causal_drivers_unavailable", "Amounts, monthly profiles, allocation and expiry are observable context. Rate, volume, scope and expiry counterfactuals are unavailable; no numerical cause is inferred.")
        result["assumptions"] = ["Allocation identifies one organization/product/business case; these are dimensions of the same spend, not additive amounts.", "Forecast CSV spend is never added to Financial Forecast."]
        return result

    @staticmethod
    def _counts(rows):
        groups = {}
        for row in rows:
            key = (row["WORK_COUNTRY"], row["WORKER_TYPE"])
            groups.setdefault(key, set()).add(row["Assignment_ID"])
        return {"representative_assignment_count": len({r["Assignment_ID"] for r in rows}),
                "groups": [{"country": k[0], "worker_type": k[1], "representative_assignment_count": len(v)} for k, v in sorted(groups.items())]}

    def run_workforce_scenario(self, *, action="baseline", percentage=0, vendor_id=None, organization_id=None,
                               country=None, market=None, worker_type=None, target_country=None,
                               target_worker_type=None, assignment_ids=None):
        if action not in ("baseline", "reduce", "shift"):
            raise StructuredDataError("Unsupported workforce action")
        pct = decimal_value(percentage)
        if not 0 <= pct <= 100:
            raise StructuredDataError("percentage must be between 0 and 100")
        if action == "baseline" and pct != 0:
            raise StructuredDataError("Baseline requires percentage zero")
        if action != "shift" and (target_country is not None or target_worker_type is not None):
            raise StructuredDataError("Targets are only valid for shifts")
        if action == "shift" and target_country is None and target_worker_type is None:
            raise StructuredDataError("Shift requires a target category")
        result = self._result("what_if", vendor_id=vendor_id, organization_id=organization_id, country=country, market=market, worker_type=worker_type)
        masters = self._portfolio(result, organization_id)
        if vendor_id is not None:
            source = self.structured.get_vendor_contract(vendor_id)
            self._merge(result, source)
            masters = [m for m in masters if m["Vendor_ID"] == vendor_id]
        rows = []
        for master in masters:
            source = self.semantic.get_vendor_workforce_context(master["Vendor_ID"])
            self._merge(result, source)
            rows.extend(source["facts"]["assignments"])
        # Validated service already enforces globally unique assignment identities.
        if len({r["Assignment_ID"] for r in rows}) != len(rows):
            raise StructuredDataError("Duplicate assignment identity across returned vendors")
        for value in (country, market, worker_type, target_country, target_worker_type):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise StructuredDataError("Scenario categories must be nonempty strings")
        for value, column in ((target_country, "WORK_COUNTRY"), (target_worker_type, "WORKER_TYPE")):
            if value is not None and value not in {r[column] for r in rows}:
                raise StructuredDataError("Target category must exist in the selected vendor/organization population")
        selected = [r for r in rows if all(v is None or r[k].casefold() == v.casefold() for k, v in (("WORK_COUNTRY", country), ("MARKET", market), ("WORKER_TYPE", worker_type)))]
        if assignment_ids is not None:
            if not isinstance(assignment_ids, (list, tuple)) or any(not isinstance(i, str) for i in assignment_ids) or len(set(assignment_ids)) != len(assignment_ids):
                raise StructuredDataError("Expected unique assignment IDs")
            if not set(assignment_ids) <= {r["Assignment_ID"] for r in selected}:
                raise StructuredDataError("Assignment IDs are outside the selected scope")
            selected = [r for r in selected if r["Assignment_ID"] in assignment_ids]
        selected.sort(key=lambda r: r["Assignment_ID"])
        eligible = [r for r in selected if action != "shift" or (target_country is not None and r["WORK_COUNTRY"] != target_country) or (target_worker_type is not None and r["WORKER_TYPE"] != target_worker_type)]
        equivalent = Decimal(len(eligible)) * pct / 100
        count = int(equivalent.quantize(Decimal(1), rounding=ROUND_HALF_UP))
        affected = eligible[:count]
        affected_ids = {r["Assignment_ID"] for r in affected}
        overlay = deepcopy(rows)
        if action == "reduce":
            overlay = [r for r in overlay if r["Assignment_ID"] not in affected_ids]
        elif action == "shift":
            for row in overlay:
                if row["Assignment_ID"] in affected_ids:
                    if target_country is not None:
                        row["WORK_COUNTRY"] = target_country
                    if target_worker_type is not None:
                        row["WORKER_TYPE"] = target_worker_type
        result["facts"] = {"baseline": self._counts(rows), "selected_baseline": self._counts(selected),
                           "eligible_assignment_count": len(eligible), "affected_assignment_count": count,
                           "affected_assignment_ids": sorted(affected_ids), "resulting_counts": self._counts(overlay),
                           "selected_resulting_counts": self._counts([r for r in overlay if r["Assignment_ID"] in {s["Assignment_ID"] for s in selected}]),
                           "financial_impact": "unavailable"}
        result["calculations"] = [{"formula": "round_half_up(eligible_distinct_assignments * requested_percentage / 100)",
                                   "requested_percentage": decimal_string(pct), "unrounded_assignment_equivalent": decimal_string(equivalent),
                                   "affected_assignment_count": count, "effective_percentage": decimal_string(Decimal(count) * 100 / len(eligible)) if eligible else None}]
        result["assumptions"] = [{"action": action, "target_country": target_country, "target_worker_type": target_worker_type,
                                  "selection": "ascending Assignment_ID; already-at-target assignments excluded", "rounding": "nearest whole assignment, halves up"},
                                 "Overlay changes counts only; no source records or graph entities are changed."]
        self._note(result, "no_valid_cost_basis", "Allocated service fees are not salary, marginal labor rates or avoidable costs. Financial impact is unavailable.")
        self._note(result, "scenario_feasibility_unassessed", "No skills, capacity, legal, contract, productivity, FTE, location/onshore reclassification or service-outcome effects are inferred. Counts describe representative assignments, not enterprise headcount.")
        if not selected:
            self._note(result, "empty_selection", "No representative assignments match the filters; this does not establish absence in the full workforce.")
        return result
