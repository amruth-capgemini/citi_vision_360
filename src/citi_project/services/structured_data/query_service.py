"""Validated namespace-bound CSV reads; no SQL, graph access or writes."""

from copy import deepcopy
import csv
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import re


NAMESPACE = "synthetic-pack-20260928"
_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
_IDENTITY = {"Graph_Namespace", "Vendor_ID", "Contract_ID", "Service_ID"}
_FILES = {
    "master": "generated/canonical_vendor_master.csv",
    "business": "generated/business_case_tech_crosswalk.csv",
    "applications": "generated/contract_application_bridge.csv",
    "organizations": "generated/organization_ou_crosswalk.csv",
    "external": "generated/vendor_external_id_crosswalk.csv",
    "forecast": "CT_Vendor_Technology_Forecast.csv",
    "financials": "CT_Technology_Financials.csv",
    "workforce": "CT_Workforce_Organization.csv",
}
_REQUIRED = {
    "master": _IDENTITY | {"Vendor_Name", "SOW_ID", "Organization_ID", "Product_ID", "BCID", "Tech_ID", "VRM_ID", "Contract_Start_Date", "Contract_End_Date", "As_Of_Date", "SLA_ID", "Risk_Assessment_ID", "Issue_ID", "Contract_Format", "Snapshot_ID"},
    "business": _IDENTITY | {"BCID", "Tech_ID", "Organization_ID", "Product_ID", "Effective_From", "Effective_To"},
    "applications": _IDENTITY | {"Application_ID", "Relationship_Type"},
    "organizations": {"Graph_Namespace", "Organization_ID", "Organization_Name", "OU_ID"},
    "external": {"Graph_Namespace", "Vendor_ID", "Contract_ID", "VRM_ID", "BCID", "Tech_ID", "Organization_ID", "OU_ID"},
    "forecast": _IDENTITY | {"record_id", "SOW_ID", "Organization_ID", "Product_ID", "BCID", "Tech_ID", "VRM_ID", "Forecast_2026_USD", "Budget_2026_USD", "Actual_YTD_2026_USD", "Col_2026_YTP", "Actual_Through", "Currency"},
    "financials": _IDENTITY | {"record_id", "SOW_ID", "Organization_ID", "Product_ID", "BCID", "Tech_ID", "Year", "Scenario", "Currency", "Amount", "Amount_Basis", "YTD_Amount", "Budget_YTD_Amount", "Period_Start", "Period_End", "Actual_Through", "FY_USD"} | {m + "_USD" for m in _MONTHS},
    "workforce": _IDENTITY | {"Assignment_ID", "SOW_ID", "Organization_ID", "Product_ID", "BCID", "Tech_ID", "Application_ID", "WORKER_TYPE", "TECH_MARKET_TYPE", "Population_Assignment_Count", "Source_Record_ID"},
}


class StructuredDataError(ValueError):
    """Invalid snapshot or unsupported input. Messages never contain row contents."""


def decimal_value(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise StructuredDataError("Expected a finite decimal value") from None
    if not result.is_finite():
        raise StructuredDataError("Expected a finite decimal value")
    return result


def decimal_string(value):
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")


def variance(value, baseline):
    delta = value - baseline
    return {"amount": decimal_string(delta), "percent": decimal_string(delta * 100 / baseline) if baseline else None}


def _date(value):
    if isinstance(value, date) and type(value) is date:
        return value
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise StructuredDataError("Expected an ISO calendar date") from None


class StructuredQueryService:
    """Eager validated snapshot. Monetary response fields are exact decimal strings.

    The master currently permits one contract per vendor. Conflicts fail rather
    than selecting an arbitrary row. Foreign namespaces are filtered before joins.
    """

    namespace = NAMESPACE

    def __init__(self, data_dir, *, max_rows=10000):
        if type(max_rows) is not int or not 1 <= max_rows <= 100000:
            raise StructuredDataError("Invalid row bound")
        self._tables = {}
        for name, relative in _FILES.items():
            self._tables[name] = self._load(Path(data_dir) / relative, name, max_rows)
        self._validate()

    @staticmethod
    def _load(path, name, max_rows):
        try:
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                fields = reader.fieldnames or []
                if len(fields) != len(set(fields)) or not _REQUIRED[name] <= set(fields):
                    raise StructuredDataError(f"Invalid columns in {name}")
                rows = []
                for index, row in enumerate(reader, 2):
                    if index - 1 > max_rows:
                        raise StructuredDataError(f"Row limit exceeded in {name}")
                    if None in row or any(v is None for v in row.values()):
                        raise StructuredDataError(f"Malformed row in {name}")
                    if row["Graph_Namespace"] == NAMESPACE:
                        rows.append({**row, "_line": index})
                return rows
        except (OSError, UnicodeError, csv.Error):
            raise StructuredDataError(f"Unable to read {name}") from None

    @staticmethod
    def _unique(rows, columns, *, deduplicate=False):
        found = {}
        for row in rows:
            key = tuple(row[k] for k in columns)
            if key in found:
                left = {k: v for k, v in found[key].items() if k != "_line"}
                right = {k: v for k, v in row.items() if k != "_line"}
                if not deduplicate or left != right:
                    raise StructuredDataError("Duplicate or conflicting canonical record")
            else:
                found[key] = row
        return list(found.values())

    def _validate(self):
        tables = self._tables
        masters = self._unique(tables["master"], ["Vendor_ID"])
        self._unique(masters, ["Contract_ID"])
        self._unique(masters, ["Service_ID"])
        self._masters = {row["Vendor_ID"]: row for row in masters}
        if not masters:
            raise StructuredDataError("Canonical namespace master is empty")
        for row in masters:
            for col, prefix, digits in [("Vendor_ID", "V", 3), ("Contract_ID", "CTR", 3), ("Service_ID", "SVC", 3), ("SOW_ID", "SOW", 3), ("Product_ID", "PROD", 2), ("Organization_ID", "ORG", 2)]:
                if not re.fullmatch(prefix + r"-\d{" + str(digits) + "}", row[col]):
                    raise StructuredDataError("Invalid canonical identifier")
            if _date(row["Contract_Start_Date"]) > _date(row["Contract_End_Date"]):
                raise StructuredDataError("Invalid contract interval")
        snapshots = {_date(r["As_Of_Date"]) for r in masters}
        if len(snapshots) != 1:
            raise StructuredDataError("Mixed master snapshot dates")
        self.as_of_date = snapshots.pop()
        orgs = {r["Organization_ID"]: r for r in self._unique(tables["organizations"], ["Organization_ID"])}
        self._unique(tables["organizations"], ["OU_ID"])
        if any(r["Organization_ID"] not in orgs for r in masters):
            raise StructuredDataError("Missing organization crosswalk")
        canonical = ["Contract_ID", "Service_ID", "SOW_ID", "Organization_ID", "Product_ID", "BCID", "Tech_ID", "VRM_ID", "Vendor_Name", "Contract_Start_Date", "Contract_End_Date", "Contract_Format", "SLA_ID", "Risk_Assessment_ID", "Issue_ID"]
        for name, rows in tables.items():
            if name == "organizations":
                continue
            for row in rows:
                master = self._masters.get(row["Vendor_ID"])
                if master is None or any(row[k] != master[k] for k in canonical if k in row):
                    raise StructuredDataError(f"Canonical join mismatch in {name}")
                if "OU_ID" in row and row["OU_ID"] != orgs[master["Organization_ID"]]["OU_ID"]:
                    raise StructuredDataError("OU crosswalk mismatch")
                if "As_Of_Date" in row and row["As_Of_Date"] != master["As_Of_Date"]:
                    raise StructuredDataError("Mixed snapshot dates")
                if row.get("Risk_Assessment_Status") == "Missing" and (row.get("Risk_Tier") or row.get("Risk_Assessment_Date")):
                    raise StructuredDataError("Missing risk must not carry a tier or date")
        for name in ("business", "external", "forecast"):
            self._unique(tables[name], ["Vendor_ID", "Contract_ID"])
        for name in ("business", "external"):
            if {r["Vendor_ID"] for r in tables[name]} != set(self._masters):
                raise StructuredDataError("Incomplete identity crosswalk")
        for row in tables["business"]:
            master = self._masters[row["Vendor_ID"]]
            if (row["Effective_From"], row["Effective_To"]) != (master["Contract_Start_Date"], master["Contract_End_Date"]):
                raise StructuredDataError("Crosswalk effective dates mismatch")
        tables["applications"] = self._unique(tables["applications"], ["Contract_ID", "Service_ID", "Application_ID", "Relationship_Type"])
        allowed_apps = set()
        for row in tables["applications"]:
            if row["Relationship_Type"] not in ("SUPPORTS", "USES_PORTAL") or not re.fullmatch(r"APP-\d{3}", row["Application_ID"]):
                raise StructuredDataError("Invalid application bridge")
            allowed_apps.add((row["Service_ID"], row["Application_ID"]))
        tables["workforce"] = self._unique(tables["workforce"], ["Assignment_ID"], deduplicate=True)
        for row in tables["workforce"]:
            if not re.fullmatch(r"ASN-\d{3}-\d{3}", row["Assignment_ID"]):
                raise StructuredDataError("Invalid assignment identity")
            if row["Application_ID"] and (row["Service_ID"], row["Application_ID"]) not in allowed_apps:
                raise StructuredDataError("Assignment application absent from service bridge")
        self._unique(tables["financials"], ["record_id"])
        self._unique(tables["financials"], ["Contract_ID", "Year", "Scenario"])
        for row in tables["financials"]:
            if row["Year"] != "2026" or row["Scenario"] not in ("Budget", "Forecast", "Actual") or row["Currency"] != "USD":
                raise StructuredDataError("Unsupported financial year, scenario or currency")
            actual = row["Scenario"] == "Actual"
            expected_end = "2026-08-31" if actual else "2026-12-31"
            if row["Period_Start"] != "2026-01-01" or row["Period_End"] != expected_end or row["Actual_Through"] != "2026-08-31":
                raise StructuredDataError("Financial periods are not comparable")
            if row["Amount_Basis"] != ("YTD Jan-Aug" if actual else "FY Jan-Dec"):
                raise StructuredDataError("Financial amount basis mismatch")
            values = [decimal_value(row[m + "_USD"]) for m in _MONTHS[:8 if actual else 12]]
            if any(v < 0 for v in values) or sum(values) != decimal_value(row["Amount"]) or sum(values[:8]) != decimal_value(row["YTD_Amount"]):
                raise StructuredDataError("Monthly financial amounts do not reconcile")
            if actual and (row["FY_USD"] or any(row[m + "_USD"] for m in _MONTHS[8:])):
                raise StructuredDataError("YTD Actual must not imply future actuals")
            if not actual and decimal_value(row["FY_USD"]) != decimal_value(row["Amount"]):
                raise StructuredDataError("Financial annual alias mismatch")
            decimal_value(row["Budget_YTD_Amount"])
        for vendor_id in self._masters:
            fs = {r["Scenario"]: r for r in tables["financials"] if r["Vendor_ID"] == vendor_id}
            budget = fs.get("Budget")
            if budget and any(decimal_value(r["Budget_YTD_Amount"]) != decimal_value(budget["YTD_Amount"]) for r in fs.values()):
                raise StructuredDataError("Comparable budget mismatch")
            if "Forecast" in fs and "Actual" in fs and any(fs["Forecast"][m + "_USD"] != fs["Actual"][m + "_USD"] for m in _MONTHS[:8]):
                raise StructuredDataError("Forecast YTD differs from Actual")
            for fc in (r for r in tables["forecast"] if r["Vendor_ID"] == vendor_id):
                if fc["Currency"] != "USD" or fc["Actual_Through"] != "2026-08-31":
                    raise StructuredDataError("Forecast currency or actual period mismatch")
                for scenario, column in [("Budget", "Budget_2026_USD"), ("Forecast", "Forecast_2026_USD"), ("Actual", "Actual_YTD_2026_USD")]:
                    value = decimal_value(fc[column])
                    if value < 0 or (scenario in fs and value != decimal_value(fs[scenario]["Amount"])):
                        raise StructuredDataError("Forecast/financial reconciliation mismatch")
                if decimal_value(fc["Actual_YTD_2026_USD"]) + decimal_value(fc["Col_2026_YTP"]) != decimal_value(fc["Forecast_2026_USD"]):
                    raise StructuredDataError("Remaining-year forecast mismatch")

    @staticmethod
    def _vendor(vendor_id):
        if not isinstance(vendor_id, str) or not re.fullmatch(r"V-\d{3}", vendor_id):
            raise StructuredDataError("Expected a canonical vendor ID")

    def _response(self, vendor_id, question_type, facts, sources=(), limitations=()):
        if vendor_id is not None:
            self._vendor(vendor_id)
        master = self._masters.get(vendor_id)
        evidence = []
        for table, row in sources:
            item = {"source_type": "structured", "source_dataset": Path(_FILES[table]).stem,
                    "source_record_id": row.get("record_id") or row.get("Assignment_ID") or row.get("Contract_ID") or row.get("Organization_ID"),
                    "source_line": row["_line"], "namespace": self.namespace,
                    "upstream_record_id": row.get("Source_Record_ID"), "upstream_file": row.get("SourceFile")}
            if item not in evidence:
                evidence.append(item)
        notes = [{"code": "synthetic_data", "message": "Structured values are synthetic scenarios, not posted actuals or approved business decisions."}, *limitations]
        if vendor_id is not None and master is None:
            notes.append({"code": "vendor_not_found", "message": "Vendor is absent from this namespace's canonical master."})
        return deepcopy({"namespace": self.namespace, "vendor_id": vendor_id,
                         "contract_id": master["Contract_ID"] if master else None,
                         "question_type": question_type, "facts": facts, "evidence": evidence, "limitations": notes})

    def _rows(self, name, vendor_id):
        self._vendor(vendor_id)
        return [r for r in self._tables[name] if r.get("Vendor_ID") == vendor_id]

    @staticmethod
    def _public(row):
        return {k: v for k, v in row.items() if not k.startswith("_")}

    def get_vendor_contract(self, vendor_id):
        rows = self._rows("master", vendor_id)
        return self._response(vendor_id, "vendor_contract", {"contract": self._public(rows[0]) if rows else None}, [("master", r) for r in rows])

    def list_vendor_contracts(self, organization_id=None):
        """Enumerate the validated master, without an expiry-window assumption."""
        if organization_id is not None and (not isinstance(organization_id, str) or not re.fullmatch(r"ORG-\d{2}", organization_id)):
            raise StructuredDataError("Expected a canonical organization ID")
        rows = sorted((r for r in self._tables["master"] if organization_id is None or r["Organization_ID"] == organization_id), key=lambda r: r["Vendor_ID"])
        notes = [] if rows else [{"code": "empty_scope", "message": "No canonical contracts match this scope."}]
        return self._response(None, "vendor_contracts", {"contracts": [self._public(r) for r in rows]}, [("master", r) for r in rows], notes)

    def get_expiring_contracts(self, days, as_of_date=None):
        if type(days) is not int or not 0 <= days <= 3650:
            raise StructuredDataError("days must be between 0 and 3650")
        as_of = self.as_of_date if as_of_date is None else _date(as_of_date)
        rows = [r for r in self._tables["master"] if 0 <= (_date(r["Contract_End_Date"]) - as_of).days <= days]
        rows.sort(key=lambda r: (r["Contract_End_Date"], r["Contract_ID"]))
        return self._response(None, "contract_expiry", {"as_of_date": as_of.isoformat(), "window_days": days,
            "contracts": [{**self._public(r), "days_to_expiry": (_date(r["Contract_End_Date"]) - as_of).days} for r in rows]}, [("master", r) for r in rows])

    def get_vendor_financials(self, vendor_id):
        rows = self._rows("financials", vendor_id)
        # Do not expose source scenario narratives as semantic recommendations.
        facts = [{k: v for k, v in self._public(r).items() if k != "Scenario_Note"} for r in rows]
        notes = [] if rows else [{"code": "missing_financials", "message": "No financial rows are available."}]
        return self._response(vendor_id, "vendor_financials", {"records": facts}, [("financials", r) for r in rows], notes)

    def get_budget_forecast_actual(self, vendor_id, year=2026):
        if type(year) is not int:
            raise StructuredDataError("year must be an integer")
        rows = [r for r in self._rows("financials", vendor_id) if r["Year"] == str(year)]
        scenarios = {r["Scenario"]: r for r in rows}
        notes = []
        facts = {"year": year, "currency": "USD", "actual_period": "2026-01-01/2026-08-31" if year == 2026 else None,
                 "budget_forecast_period": "2026-01-01/2026-12-31" if year == 2026 else None,
                 "budget": None, "forecast": None, "actual_ytd": None, "comparable_ytd_budget": None,
                 "forecast_variance_amount": None, "forecast_variance_pct": None,
                 "actual_ytd_variance_amount": None, "actual_ytd_variance_pct": None}
        for scenario, field in [("Budget", "budget"), ("Forecast", "forecast"), ("Actual", "actual_ytd")]:
            if scenario in scenarios:
                facts[field] = decimal_string(decimal_value(scenarios[scenario]["Amount"]))
            else:
                notes.append({"code": "missing_scenario", "message": f"{scenario} is unavailable for the requested year."})
        if "Budget" in scenarios:
            facts["comparable_ytd_budget"] = decimal_string(decimal_value(scenarios["Budget"]["YTD_Amount"]))
        for value, base, prefix in [("forecast", "budget", "forecast"), ("actual_ytd", "comparable_ytd_budget", "actual_ytd")]:
            if facts[value] is not None and facts[base] is not None:
                result = variance(decimal_value(facts[value]), decimal_value(facts[base]))
                facts[prefix + "_variance_amount"], facts[prefix + "_variance_pct"] = result["amount"], result["percent"]
                if result["percent"] is None:
                    notes.append({"code": "zero_budget", "message": f"{prefix} variance percent is undefined for a zero baseline."})
        notes.append({"code": "financial_periods", "message": "Budget/Forecast are full-year; Actual is January-August. Compare Actual only with comparable YTD Budget. Forecast CSV is not added to financial Forecast."})
        return self._response(vendor_id, "budget_forecast_actual", facts, [("financials", r) for r in rows], notes)

    def get_vendor_financial_variance(self, vendor_id, year=2026):
        result = self.get_budget_forecast_actual(vendor_id, year)
        result["question_type"] = "financial_variance"
        return result

    def get_vendor_workforce(self, vendor_id):
        rows = self._rows("workforce", vendor_id)
        notes = [{"code": "representative_workforce", "message": "Count covers distinct assignments in this representative POC subset, not complete enterprise headcount."}]
        if not rows:
            notes.append({"code": "missing_workforce", "message": "No representative assignments were returned; this does not establish absence of workforce dependency."})
        return self._response(vendor_id, "vendor_workforce", {"assignments": [self._public(r) for r in rows],
            "representative_workforce_count": len({r["Assignment_ID"] for r in rows})}, [("workforce", r) for r in rows],
            notes)

    def get_vendor_workforce_count(self, vendor_id):
        result = self.get_vendor_workforce(vendor_id)
        result["facts"].pop("assignments")
        result["question_type"] = "representative_workforce_count"
        return result

    def get_workforce_groups(self, group_by, vendor_id=None):
        aliases = {"Worker_Type": "WORKER_TYPE", "Onshore_Offshore": "TECH_MARKET_TYPE"}
        allowed = {"Vendor_ID", "Contract_ID", "Service_ID", "Organization_ID", "Product_ID", "WORKER_TYPE", "TECH_MARKET_TYPE"}
        if isinstance(group_by, str):
            group_by = [group_by]
        if not isinstance(group_by, (list, tuple)) or not group_by or len(group_by) > 7 or any(not isinstance(k, str) for k in group_by):
            raise StructuredDataError("Invalid workforce grouping")
        columns = [aliases.get(k, k) for k in group_by]
        if not set(columns) <= allowed or len(columns) != len(set(columns)):
            raise StructuredDataError("Unsupported workforce grouping")
        rows = self._rows("workforce", vendor_id) if vendor_id is not None else self._tables["workforce"]
        groups = {}
        for row in rows:
            groups.setdefault(tuple(row[c] for c in columns), set()).add(row["Assignment_ID"])
        return self._response(vendor_id, "workforce_groups", {"groups": [{**dict(zip(columns, key)), "representative_workforce_count": len(ids)} for key, ids in sorted(groups.items())]}, [("workforce", r) for r in rows],
            [{"code": "representative_workforce", "message": "Grouped counts describe distinct POC assignments, not enterprise headcount."}])

    def _dimension(self, vendor_id, field, intent):
        rows = self._rows("master", vendor_id)
        return self._response(vendor_id, intent, {field: sorted({r[field] for r in rows})}, [("master", r) for r in rows])

    def get_vendor_services(self, vendor_id):
        return self._dimension(vendor_id, "Service_ID", "vendor_services")

    def get_vendor_products(self, vendor_id):
        result = self._dimension(vendor_id, "Product_ID", "vendor_products")
        result["limitations"].append({"code": "financial_product", "message": "These are financial allocation products; applications may enable other products."})
        return result

    def get_vendor_organization(self, vendor_id):
        result = self._dimension(vendor_id, "Organization_ID", "vendor_organization")
        return result

    def get_vendor_forecast(self, vendor_id):
        rows = self._rows("forecast", vendor_id)
        notes = [{"code": "duplicate_spend_representation", "message": "Forecast CSV and financial Forecast represent the same modeled spend; do not add them."}]
        if not rows:
            notes.append({"code": "missing_forecast", "message": "No forecast CSV record was returned."})
        return self._response(vendor_id, "vendor_forecast", {"records": [{k: v for k, v in self._public(r).items() if k != "Scenario_Note"} for r in rows]}, [("forecast", r) for r in rows],
            notes)

    def get_vendor_application_bridge(self, vendor_id):
        rows = self._rows("applications", vendor_id)
        return self._response(vendor_id, "vendor_application_bridge", {"applications": [self._public(r) for r in rows]}, [("applications", r) for r in rows])
