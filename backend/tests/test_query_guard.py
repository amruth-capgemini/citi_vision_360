"""Guards for model-written SQL and Cypher: what may run, and the exact text that runs."""

import pytest

from explore_fakes import schema_index
from citi_project.services.agents.query_guard import QueryRejected, SchemaIndex, guard_cypher, guard_sql

PII = ("worker_alias", "email")


@pytest.fixture(scope="module")
def index():
    return schema_index()


@pytest.mark.parametrize("query,reason", [
    ("INSERT INTO clm.canonical_vendor_master VALUES ('x')", "Only SELECT"),
    ("DROP TABLE clm.canonical_vendor_master", "Only SELECT"),
    ("SELECT 1; DROP TABLE clm.canonical_vendor_master", "Exactly one"),
    ("WITH d AS (DELETE FROM clm.canonical_vendor_master RETURNING *) SELECT * FROM d", "Delete is not allowed"),
    ('SELECT "Vendor_ID" INTO scratch FROM clm.canonical_vendor_master', "Into is not allowed"),
    ('SELECT "Vendor_ID" FROM clm.canonical_vendor_master FOR UPDATE', "Lock is not allowed"),
    ("COPY clm.canonical_vendor_master TO '/tmp/x'", "Only SELECT"),
    ("SET statement_timeout = 0", "Only SELECT"),
    ("SELECT usename FROM pg_catalog.pg_user", "Unknown dataset pg_catalog.pg_user"),
    ("SELECT table_name FROM information_schema.tables", "Unknown dataset"),
    ('SELECT "Nope" FROM clm.canonical_vendor_master', "Unknown column Nope"),
    ('SELECT "NAME" FROM workforce.ct_workforce_organization', "masked"),
    ('SELECT w."EMPLID" FROM workforce.ct_workforce_organization w', "masked"),
    ("SELECT * FROM workforce.ct_workforce_organization", "SELECT \\* is not allowed"),
    ("WITH w AS (SELECT * FROM workforce.ct_workforce_organization) SELECT \"Assignment_ID\" FROM w", "SELECT \\* is not allowed"),
    ("SELECT vendor_name FROM workforce.ct_workforce_organization", "ambiguous"),
    ("SELECT pg_read_file('/etc/passwd') FROM clm.canonical_vendor_master", "pg_read_file"),
    ("SELECT pg_sleep(10) FROM clm.canonical_vendor_master", "pg_sleep"),
    ("SELECT set_config('statement_timeout', '0', false) FROM clm.canonical_vendor_master", "set_config"),
    ("SELECT dblink('host=x', 'select 1') FROM clm.canonical_vendor_master", "dblink"),
    ('SELECT current_date, "Vendor_ID" FROM clm.canonical_vendor_master', "CURRENTDATE"),
    ("SELECT 1", "at least one catalog dataset"),
    ("SELECT FROM WHERE", "could not be parsed"),
    ("", "nonempty"),
])
def test_sql_guard_rejects(index, query, reason):
    with pytest.raises(QueryRejected, match=reason):
        guard_sql(query, index)


def test_sql_guard_requotes_identifiers_and_caps_rows(index):
    guarded = guard_sql("select contract_id, contract_end_date, _source_line from CLM.Canonical_Vendor_Master "
                        "where vendor_id = 'V-001' limit 500", index, max_rows=50)
    assert guarded.sql == ('SELECT "Contract_ID", "Contract_End_Date", "_source_line" FROM "clm"."canonical_vendor_master" '
                           "WHERE \"Vendor_ID\" = 'V-001' LIMIT 51")
    assert guarded.datasets == ("clm.canonical_vendor_master",)
    assert ("clm.canonical_vendor_master", "Contract_End_Date") in guarded.columns


def test_sql_guard_keeps_a_smaller_limit_and_wraps_unions(index):
    assert guard_sql('SELECT "Vendor_ID" FROM clm.canonical_vendor_master LIMIT 5', index).sql.endswith("LIMIT 5")
    union = guard_sql('SELECT "Vendor_ID" FROM clm.canonical_vendor_master UNION SELECT "Vendor_ID" FROM finance.ct_technology_financials', index)
    assert union.sql.startswith("SELECT * FROM (") and union.sql.endswith(") AS guarded_query LIMIT 51")
    assert union.datasets == ("clm.canonical_vendor_master", "finance.ct_technology_financials")


def test_sql_guard_accepts_joins_aggregates_aliases_and_casts(index):
    guarded = guard_sql(
        'WITH india AS (SELECT w."Contract_ID", w."WORKER_TYPE" FROM workforce.ct_workforce_organization w '
        "WHERE w.\"WORK_COUNTRY\" = 'India') "
        'SELECT m."Vendor_ID", i."WORKER_TYPE", COUNT(*) AS n, SUM(CAST(f."Col_2026_YTP" AS numeric)) AS ytp '
        'FROM india i JOIN clm.canonical_vendor_master m ON m."Contract_ID" = i."Contract_ID" '
        'JOIN finance.ct_vendor_technology_forecast f ON f."Contract_Number" = m."Contract_ID" '
        "GROUP BY 1, 2 ORDER BY n DESC", index)
    assert set(guarded.datasets) == {"workforce.ct_workforce_organization", "clm.canonical_vendor_master",
                                     "finance.ct_vendor_technology_forecast"}
    assert guarded.sql.endswith("LIMIT 51")


def test_sql_guard_allows_boolean_operators_and_exists(index):
    # Regression: sqlglot models AND/OR as Func subclasses, which the function allow-list once refused.
    guarded = guard_sql('SELECT m."Vendor_ID" FROM clm.canonical_vendor_master m WHERE m."Vendor_ID" = \'V-001\' '
                        'AND (m."Contract_ID" = \'CTR-001\' OR NOT m."Contract_ID" IS NULL) AND EXISTS '
                        '(SELECT 1 FROM finance.ct_vendor_technology_forecast f WHERE f."Contract_Number" = m."Contract_ID")', index)
    assert " AND " in guarded.sql and " OR " in guarded.sql and "EXISTS" in guarded.sql


def test_sql_guard_resolves_columns_in_their_own_union_branch(index):
    # Regression: a column was accepted because another UNION branch's table had it; PostgreSQL then failed.
    with pytest.raises(QueryRejected, match="Unknown column Col_2026_YTP in clm.canonical_vendor_master"):
        guard_sql('SELECT "Contract_ID", "Col_2026_YTP" FROM finance.ct_vendor_technology_forecast UNION '
                  'SELECT "Contract_ID", "Col_2026_YTP" FROM clm.canonical_vendor_master', index)
    # An outer query's column is still visible inside a correlated subquery.
    guard_sql('SELECT m."Contract_ID" FROM clm.canonical_vendor_master m WHERE EXISTS (SELECT 1 FROM '
              'finance.ct_vendor_technology_forecast f WHERE f."Contract_Number" = m."Contract_ID" AND "Col_2026_YTP" IS NOT NULL)', index)


def test_sql_guard_date_windows_use_snapshot_literals_not_the_clock(index):
    window = ('SELECT "Contract_ID" FROM clm.canonical_vendor_master WHERE CAST("Contract_End_Date" AS date) '
              "BETWEEN DATE '2026-09-28' AND DATE '2026-09-28' + 90")
    assert "2026-09-28" in guard_sql(window, index).sql
    for clock in ("current_date", "now()", "CURRENT_TIMESTAMP", "localtimestamp"):
        with pytest.raises(QueryRejected, match="not allowed"):
            guard_sql(window.replace("DATE '2026-09-28' + 90", clock), index)


def test_sql_guard_suggests_close_column_names(index):
    with pytest.raises(QueryRejected, match="similar columns: Contract_End_Date"):
        guard_sql('SELECT "Contract_End" FROM clm.canonical_vendor_master', index)


def test_sql_guard_regenerates_text_so_comments_cannot_smuggle(index):
    guarded = guard_sql('SELECT "Vendor_ID" FROM clm.canonical_vendor_master -- ; DROP TABLE x', index)
    assert "DROP" not in guarded.sql


@pytest.mark.parametrize("query,reason", [
    ("MATCH (c:Contract) RETURN c", "\\$namespace"),
    ("MATCH (c:Contract {_kg_namespace: 'kg-hardening-phase1'}) RETURN c", "_kg_namespace may only"),
    ("MATCH (c:Contract) WHERE c._kg_namespace <> $namespace RETURN c", "_kg_namespace may only"),
    ("MATCH (c:Contract {_kg_namespace: $namespace}) SET c.x = 1 RETURN c", "SET is not allowed"),
    ("MATCH (c:Contract {_kg_namespace: $namespace}) DETACH DELETE c RETURN 1", "not allowed"),
    ("CREATE (x:Vendor {_kg_namespace: $namespace}) RETURN x", "CREATE is not allowed"),
    ("MERGE (x:Vendor {_kg_namespace: $namespace}) RETURN x", "MERGE is not allowed"),
    ("CALL db.labels() YIELD label RETURN label", "CALL is not allowed"),
    ("MATCH (v:Vendor {_kg_namespace: $namespace}) RETURN apoc.convert.toJson(v)", "apoc"),
    ("LOAD CSV FROM 'file:///x' AS row RETURN row", "LOAD is not allowed"),
    ("USE system MATCH (n {_kg_namespace: $namespace}) RETURN n", "USE is not allowed"),
    ("MATCH (a:Vendor {_kg_namespace: $namespace}), (b:Contract) RETURN a, b", "Every pattern"),
    ("MATCH (a:Vendor {_kg_namespace: $namespace}) MATCH (b:Contract)-[:HAS_SOW]->(s) RETURN a, b", "Every pattern"),
    ("MATCH (a:Vendor {_kg_namespace: $namespace}) RETURN [(x:Contract)-[:HAS_SOW]->(s) | s.sow_id]", "Every pattern"),
    ("MATCH (n:Vendor|Contract {_kg_namespace: $namespace}) RETURN n", "Unsupported MATCH pattern"),
    ("MATCH (a:Assignment {_kg_namespace: $namespace}) RETURN a.worker_alias", "personal data"),
    ("MATCH (a:Assignment {_kg_namespace: $namespace}) RETURN properties(a)", "properties"),
    ("MATCH (v:Vendor {_kg_namespace: $namespace, vendor_id: $vendor}) RETURN v", "literals"),
    ("MATCH (v:Vendor {_kg_namespace: $namespace}) RETURN v; MATCH (n) RETURN n", "Exactly one"),
    ("MATCH (v:Vendor {_kg_namespace: $namespace})", "RETURN"),
])
def test_cypher_guard_rejects(query, reason):
    with pytest.raises(QueryRejected, match=reason):
        guard_cypher(query, graph="business", pii_properties=PII)


@pytest.mark.parametrize("query,labels", [
    ("MATCH (v:Vendor {_kg_namespace: $namespace, vendor_id: 'V-001'})-[:PARTY_TO]->(c:Contract) RETURN v, c.contract_id",
     ("Contract", "Vendor")),
    ("MATCH (v:Vendor {_kg_namespace: $namespace}) WITH v AS vendor "
     "MATCH (vendor)<-[:ASSESSES]-(r:RiskAssessment) RETURN r.status", ("RiskAssessment", "Vendor")),
    ("MATCH (a:Vendor {_kg_namespace: $namespace}) WHERE EXISTS { MATCH (a)-[:PARTY_TO]->(c:Contract) } RETURN a.vendor_id",
     ("Contract", "Vendor")),
    ("MATCH p = (v:Vendor {_kg_namespace: $namespace})-[*1..2]-(x) RETURN p LIMIT 5", ("Vendor",)),
    ("OPTIONAL MATCH (c:Contract {_kg_namespace: $namespace})-[:HAS_CLAUSE]->(k) RETURN c.contract_id, k", ("Contract",)),
])
def test_cypher_guard_accepts_anchored_patterns(query, labels):
    assert guard_cypher(query, graph="business", pii_properties=PII).labels == labels


def test_cypher_guard_strips_comments_and_checks_strings_as_data():
    guarded = guard_cypher("MATCH (v:Vendor {_kg_namespace: $namespace, legal_name: 'SET CREATE'}) // SET v.x = 1\n"
                           "RETURN v.vendor_id", graph="business")
    assert "//" not in guarded.cypher and "'SET CREATE'" in guarded.cypher
    with pytest.raises(QueryRejected, match="graph must be"):
        guard_cypher("MATCH (v:Vendor {_kg_namespace: $namespace}) RETURN v", graph="system")


def test_schema_index_links_follow_inferred_foreign_keys(index):
    links = {(l["dataset"], l["field"]) for l in index.links("clm.canonical_vendor_master", "Contract_ID")}
    assert ("finance.ct_vendor_technology_forecast", "Contract_Number") in links
    assert ("workforce.ct_workforce_organization", "Contract_ID") in links
    child = index.links("finance.ct_vendor_technology_forecast", "Contract_Number")
    assert child[0] == {"dataset": "clm.canonical_vendor_master", "field": "Contract_ID", "via": "clm.canonical_vendor_master.Contract_ID",
                        "basis": child[0]["basis"], "review_state": child[0]["review_state"]}
    assert index.concept("clm.canonical_vendor_master", "Contract_End_Date") == "Contract.end_date"


def test_schema_index_search_is_compact_and_flags_masked_fields(index):
    results = index.search(concepts=["Assignment.assignment_id"], limit=3)
    workforce = next(r for r in results if r["dataset"] == "workforce.ct_workforce_organization")
    assert workforce["masked_fields"] == ["EMPLID", "NAME"]
    assert len(workforce["fields"]) <= 25
    listed = index.search(datasets=["finance.ct_vendor_technology_forecast"], limit=1)[0]
    assert listed["field_count"] == len(listed["fields"]) == 113


def test_narrative_checks_values_against_cited_cards():
    from citi_project.services.agents.grounding import verify_narrative
    cards = {"F1": {"text": "V-001: contract expires 2026-12-27 (90 days from 2026-09-28); service SVC-001, SOW SOW-001."},
             "F2": {"text": "V-001: full-year 2026 Budget USD 9890410.98; forecast minus budget USD 230136.98 (2.33%)."}}

    def check(text, ids=("F1",), summary="Summary."):
        return verify_narrative({"summary": summary, "paragraphs": [{"heading": "H", "text": text, "fact_ids": list(ids)}]},
                                cards, "Which contracts expire in the next 90 days?")
    assert check("The contract ends on 27 December 2026, 90 days after the 2026-09-28 snapshot, under SOW-001.") == []
    assert check("Forecast is USD 230,136.98 (2.33%) above the USD 9,890,410.98 budget.", ["F2"]) == []
    assert check("It ends on December 27th, 2026 [F1].") == []
    assert check("It ends on 28 December 2026.")[0].startswith("paragraph 1: date 2026-12-28 is not in its cited facts")
    assert check("Forecast is USD 230,136.98 above budget.", ["F1"])[0].endswith("it is in F2; add that card to fact_ids")
    assert "value 230,000" in check("Roughly USD 230,000 over.", ["F2"])[0]
    assert check("Under SOW-002.")[0] == "paragraph 1: identifier SOW-002 is in no card; remove it"
    assert check("It sits beside SVC-001 and budget line V-001.", ["F2"]) == []  # IDs from any card are allowed
    assert "recommendation" in check("The contract should be renewed.")[0]
    # The summary may use any card's values, but nothing that is in no card.
    assert check("Budget detail.", ["F2"], summary="Budget is USD 9,890,410.98 and it expires 2026-12-27.") == []
    assert "value 12.5 is in no card" in check("Budget detail.", ["F2"], summary="Spend is 12.5% over.")[0]


def test_schema_index_from_catalog_rows():
    rows = [{"dataset_id": "clm.x", "grain": "g", "row_count": 1, "primary_key": ["A"], "description": None,
             "fields": [{"name": "A", "business_name": "A", "concept": "Vendor.vendor_id", "role": "identifier", "masked": False,
                         "references": None, "basis": None, "review_state": None}]}]
    index = SchemaIndex.from_rows(rows)
    assert guard_sql("SELECT a FROM clm.x", index).sql == 'SELECT "A" FROM "clm"."x" LIMIT 51'
