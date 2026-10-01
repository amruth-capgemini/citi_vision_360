"""Physical layout: one schema per simulated source system."""

from dataclasses import dataclass

from ..structured_data.query_service import _FILES

READER_ROLE = "citi_reader"
META_SCHEMA = "citi_meta"
SYSTEMS = {
    "clm": "Contract lifecycle management (simulates Icertis/Sirion)",
    "finance": "Procurement and general ledger (simulates Coupa/Ariba/SAP GL)",
    "workforce": "Contingent workforce management (simulates Fieldglass/Beeline)",
    "mdm": "ERP master data crosswalks",
    "dependency": "Configuration management database (simulates a CMDB)",
}
LINE, FILE = "_source_line", "_source_file"


@dataclass(frozen=True)
class Table:
    name: str
    schema: str
    table: str
    key: tuple  # primary key; LINE where the file has no unique business grain

    @property
    def source_file(self):
        return _FILES[self.name]


TABLES = {t.name: t for t in (
    Table("master", "clm", "canonical_vendor_master", ("Graph_Namespace", "Vendor_ID")),
    Table("business", "clm", "business_case_tech_crosswalk", ("Graph_Namespace", "Vendor_ID", "Contract_ID")),
    Table("financials", "finance", "ct_technology_financials", ("Graph_Namespace", "record_id")),
    Table("forecast", "finance", "ct_vendor_technology_forecast", ("Graph_Namespace", "record_id")),
    Table("workforce", "workforce", "ct_workforce_organization", ("Graph_Namespace", "Assignment_ID")),
    Table("organizations", "mdm", "organization_ou_crosswalk", ("Graph_Namespace", "Organization_ID")),
    Table("external", "mdm", "vendor_external_id_crosswalk", ("Graph_Namespace", "Vendor_ID", "Contract_ID")),
    Table("applications", "dependency", "contract_application_bridge", (LINE,)),
)}
assert set(TABLES) == set(_FILES)
