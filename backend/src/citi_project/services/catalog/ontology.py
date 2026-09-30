"""Catalog ontology, namespace and business-domain vocabulary."""

from pathlib import Path

from ..ontology import OntologyRegistry
from ..ontology.registry import SCHEMA_FILE, default_ontology_dir

CATALOG_NAMESPACE = "catalog-synthetic-pack-20260928"
CANONICAL_NAMESPACE = "synthetic-pack-20260928"
NAMESPACE_COLUMN = "Graph_Namespace"

# domain_id -> (display name, data-pack source class, description)
DOMAINS = {
    "vendor_ownership": ("Vendor ownership", "01_vendor_ownership",
                         "Who the vendor is, identity crosswalks, and the buyer organizations, products and owners."),
    "contracts_sows": ("Contracts and SOWs", "02_contracts_sows",
                       "Agreements, statements of work, terms, dates and clause obligations."),
    "spend_forecast": ("Spend and forecast", "03_spend_forecast",
                       "Budget, forecast, actual spend, invoices, commitments and allocations."),
    "workforce": ("Workforce", "04_workforce",
                  "Vendor workforce assignments, roles, locations and allocated fees."),
    "service_dependencies": ("Service dependencies", "05_service_dependencies",
                             "Services, applications, configuration items and business processes they support."),
    "risk_sla_performance": ("Risk, SLA and performance", "06_risk_sla_performance",
                             "Risk assessments, issues, service levels and performance measurements."),
}
# Default domain of each simulated source system (Postgres schema).
SCHEMA_DOMAINS = {"clm": "contracts_sows", "finance": "spend_forecast", "workforce": "workforce",
                  "mdm": "vendor_ownership", "dependency": "service_dependencies"}
# Domain of a business ontology class, by the module that defines it.
MODULE_DOMAINS = {"core": "vendor_ownership", "commercial": "spend_forecast", "contracts": "contracts_sows",
                  "workforce": "workforce", "technology": "service_dependencies", "risk": "risk_sla_performance"}
CLASS_DOMAIN_OVERRIDES = {"Vendor": "vendor_ownership", "ParentEntity": "vendor_ownership"}


def catalog_ontology_dir():
    return default_ontology_dir() / "catalog"


def load_catalog_registry(directory=None):
    """The catalog registry has its own ontology_version, independent of the business ontology."""
    directory = Path(directory) if directory is not None else catalog_ontology_dir()
    return OntologyRegistry.load(directory, schema_path=directory.parent / SCHEMA_FILE)


def class_domain(registry, class_id):
    return CLASS_DOMAIN_OVERRIDES.get(class_id) or MODULE_DOMAINS.get(registry.get_class(class_id).module)
