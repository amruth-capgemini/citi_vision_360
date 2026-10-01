"""Canonical CSV regression tests. Mutations affect temporary copies only."""

import csv
from decimal import Decimal
from pathlib import Path
import shutil

import pytest

from citi_project.services.structured_data import StructuredDataError, StructuredQueryService
from citi_project.services.structured_data.query_service import _FILES, variance

DATA = Path(__file__).resolve().parents[2] / "initial_plan"


@pytest.fixture
def service():
    return StructuredQueryService(DATA)


@pytest.fixture
def copied(tmp_path):
    for relative in _FILES.values():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DATA / relative, target)
    return tmp_path


def mutate(directory, table, change):
    path = directory / _FILES[table]
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields, rows = reader.fieldnames, list(reader)
    change(rows)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_twenty_canonical_joins_and_no_financial_fanout(service):
    total = Decimal(0)
    for i in range(1, 21):
        vendor = f"V-{i:03}"
        master = service.get_vendor_contract(vendor)["facts"]["contract"]
        assert master["Contract_ID"] == f"CTR-{i:03}"
        assert service.get_vendor_services(vendor)["facts"]["Service_ID"] == [master["Service_ID"]]
        assert service.get_vendor_products(vendor)["facts"]["Product_ID"] == [master["Product_ID"]]
        assert service.get_vendor_organization(vendor)["facts"]["Organization_ID"] == [master["Organization_ID"]]
        financial = service.get_budget_forecast_actual(vendor)
        total += Decimal(financial["facts"]["forecast"])
        assert len(financial["evidence"]) == 3
        assert len({e["source_record_id"] for e in financial["evidence"]}) == 3
        assert service.get_vendor_forecast(vendor)["facts"]["records"][0]["Forecast_2026_USD"] == financial["facts"]["forecast"]
    assert total == Decimal("55228019.17")


def test_variances_are_period_comparable(service):
    f = service.get_vendor_financial_variance("V-005")["facts"]
    assert (f["budget"], f["forecast"], f["forecast_variance_amount"], f["forecast_variance_pct"]) == ("2585753.42", "2880673.98", "294920.56", "11.41")
    f = service.get_budget_forecast_actual("V-009")["facts"]
    assert (f["actual_ytd"], f["comparable_ytd_budget"], f["actual_ytd_variance_amount"], f["actual_ytd_variance_pct"]) == ("3728219.13", "3328767.09", "399452.04", "12.00")
    assert f["budget"] != f["comparable_ytd_budget"]


def test_expiry_uses_snapshot_and_inclusive_boundary(service):
    result = service.get_expiring_contracts(90)
    assert result["facts"]["as_of_date"] == "2026-09-28"
    assert [(r["Vendor_ID"], r["days_to_expiry"]) for r in result["facts"]["contracts"]] == [("V-005", 15), ("V-018", 30), ("V-009", 45), ("V-007", 60), ("V-013", 75), ("V-001", 90)]
    assert [r["Vendor_ID"] for r in service.get_expiring_contracts(0, "2026-10-13")["facts"]["contracts"]] == ["V-005"]
    assert not service.get_expiring_contracts(0, "2026-10-14")["facts"]["contracts"]


@pytest.mark.parametrize("column", ["Vendor_ID", "Contract_ID", "Service_ID", "Organization_ID", "Product_ID", "Worker_Type", "Onshore_Offshore"])
def test_workforce_groupings(service, column):
    groups = service.get_workforce_groups(column)["facts"]["groups"]
    assert sum(g["representative_workforce_count"] for g in groups) == 60
    assert service.get_vendor_workforce_count("V-001")["facts"]["representative_workforce_count"] == 4
    assert service.get_vendor_workforce_count("V-017")["facts"]["representative_workforce_count"] == 2


def test_exact_assignment_duplicates_count_once(copied):
    mutate(copied, "workforce", lambda rows: rows.append(dict(rows[0])))
    result = StructuredQueryService(copied).get_vendor_workforce("V-001")
    assert result["facts"]["representative_workforce_count"] == 4
    assert len(result["facts"]["assignments"]) == 4
    assert "representative_workforce" in {n["code"] for n in result["limitations"]}


@pytest.mark.parametrize("table,column,value", [
    ("financials", "Contract_ID", "CTR-020"),
    ("workforce", "Service_ID", "SVC-020"),
    ("forecast", "Organization_ID", "ORG-99"),
    ("financials", "Product_ID", "PROD-99"),
    ("workforce", "SOW_ID", "SOW-020"),
    ("workforce", "Application_ID", "APP-999"),
    ("financials", "OU_ID", "ORG-01"),
    ("financials", "Amount", "NaN"),
    ("financials", "Budget_YTD_Amount", "1"),
    ("forecast", "Forecast_2026_USD", "1"),
    ("business", "Effective_To", "2029-01-01"),
    ("master", "As_Of_Date", "2026-10-01"),
    ("applications", "Relationship_Type", "INVENTED"),
])
def test_invalid_data_rejected(copied, table, column, value):
    mutate(copied, table, lambda rows: rows[0].update({column: value}))
    with pytest.raises(StructuredDataError):
        StructuredQueryService(copied)


@pytest.mark.parametrize("table", ["master", "financials", "applications"])
def test_duplicate_grains_rejected(copied, table):
    mutate(copied, table, lambda rows: rows.append(dict(rows[0])))
    with pytest.raises(StructuredDataError):
        StructuredQueryService(copied)


def test_conflicting_assignment_rejected(copied):
    mutate(copied, "workforce", lambda rows: rows.append({**rows[0], "WORKER_TYPE": "conflicting"}))
    with pytest.raises(StructuredDataError):
        StructuredQueryService(copied)


def test_namespace_isolation(copied):
    for table in _FILES:
        mutate(copied, table, lambda rows: rows.append({**rows[0], "Graph_Namespace": "kg-hardening-phase1"}))
    s = StructuredQueryService(copied)
    assert len(s.get_vendor_financials("V-001")["facts"]["records"]) == 3
    assert s.get_vendor_workforce_count("V-001")["facts"]["representative_workforce_count"] == 4


def test_missing_budget_is_not_substituted_from_forecast(copied):
    mutate(copied, "financials", lambda rows: rows.__setitem__(slice(None), [r for r in rows if not (r["Vendor_ID"] == "V-001" and r["Scenario"] == "Budget")]))
    result = StructuredQueryService(copied).get_budget_forecast_actual("V-001")
    assert result["facts"]["budget"] is None
    assert result["facts"]["comparable_ytd_budget"] is None
    assert result["facts"]["actual_ytd_variance_amount"] is None
    assert "missing_scenario" in {n["code"] for n in result["limitations"]}


def test_zero_baseline_and_negative_variance():
    assert variance(Decimal(2), Decimal(0)) == {"amount": "2.00", "percent": None}
    assert variance(Decimal(90), Decimal(100)) == {"amount": "-10.00", "percent": "-10.00"}


def test_unknown_vendor_year_and_mutation_isolation(service):
    assert service.get_vendor_contract("V-999")["facts"]["contract"] is None
    assert service.get_budget_forecast_actual("V-001", 2027)["facts"]["actual_ytd"] is None
    result = service.get_vendor_contract("V-001")
    result["facts"]["contract"]["Vendor_Name"] = "changed"
    assert service.get_vendor_contract("V-001")["facts"]["contract"]["Vendor_Name"] == "Aurelix Codeworks"


@pytest.mark.parametrize("operation", [lambda s: s.get_vendor_contract("Aurelix Codeworks"), lambda s: s.get_expiring_contracts(-1), lambda s: s.get_expiring_contracts(True), lambda s: s.get_expiring_contracts(90, "bad"), lambda s: s.get_workforce_groups("NAME"), lambda s: s.get_budget_forecast_actual("V-001", "2026")])
def test_bounded_inputs(service, operation):
    with pytest.raises(StructuredDataError):
        operation(service)
