"""Offline PostgreSQL loader and reader tests using an in-memory fake connection."""

from contextlib import contextmanager, nullcontext
import csv
from pathlib import Path
import re
import shutil

import pytest

from citi_project.services.postgres import PostgresConfig, PostgresConfigurationError, TABLES
from citi_project.services.postgres import cli, loader
from citi_project.services.postgres.config import sanitized
from citi_project.services.structured_data import StructuredDataError, StructuredQueryService
from citi_project.services.structured_data.query_service import _FILES

DATA = Path(__file__).resolve().parents[2] / "initial_plan"
READER = PostgresConfig(reader="dbname=citi_vdi user=reader-placeholder")
_IDENT = re.compile(r'"((?:[^"]|"")*)"')


def render(query):
    return query if isinstance(query, str) else query.as_string(None)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class FakeConnection:
    """Just enough PostgreSQL for the loader and reader: tables, a manifest and COPY."""

    def __init__(self):
        self.tables, self.manifest, self.statements, self.params = {}, {}, [], []
        self.roles, self.read_only = set(), False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def transaction(self):
        return nullcontext()

    def execute(self, query, params=None):
        text = render(query)
        self.statements.append(text)
        self.params.append(params)
        idents = [i.replace('""', '"') for i in _IDENT.findall(text)]
        if text.startswith("SELECT 1 FROM pg_roles"):
            return Result([(1,)] if params[0] in self.roles else [])
        if text.startswith("CREATE ROLE"):
            self.roles.add(idents[0])
        elif "information_schema.columns" in text:
            table = self.tables.get(tuple(params))
            return Result([(c,) for c in table["columns"]] if table else [])
        elif text.startswith('SELECT "_source_line"'):
            table, wanted = self.tables[(idents[-3], idents[-2])], idents[:-3]
            index = [table["columns"].index(c) for c in wanted]
            rows = sorted(([r[i] for i in index] for r in table["rows"]), key=lambda r: r[0])
            return Result(rows[:params[0]] if params else rows)
        elif text.startswith("SELECT file_sha256"):
            return Result([self.manifest[params[0]]] if params[0] in self.manifest else [])
        elif text.startswith('INSERT INTO "citi_meta"."load_manifest"'):
            self.manifest[params[0]] = params[4:]
        elif text.startswith("DROP TABLE"):
            self.tables.pop((idents[0], idents[1]), None)
        elif text.startswith("CREATE TABLE") and idents[:2] != ["citi_meta", "load_manifest"]:
            self.tables[(idents[0], idents[1])] = {"columns": None, "rows": []}
        return Result([])

    @contextmanager
    def cursor(self):
        yield self

    @contextmanager
    def copy(self, statement):
        text = render(statement)
        idents = _IDENT.findall(text)
        table = self.tables[(idents[0], idents[1])]
        table["columns"] = [i.replace('""', '"') for i in idents[2:]]
        self.statements.append(text)
        yield _Copy(table)


class _Copy:
    def __init__(self, table):
        self.table = table

    def write_row(self, values):
        assert len(values) == len(self.table["columns"])
        self.table["rows"].append(list(values))


@pytest.fixture
def loaded():
    conn = FakeConnection()
    loader.init(conn)
    loader.load(conn, DATA)
    return conn


def test_read_csv_matches_dictreader_lines_and_values():
    for name, spec in TABLES.items():
        fields, rows, digest = loader.read_csv(DATA / spec.source_file, name)
        with (DATA / _FILES[name]).open(encoding="utf-8-sig", newline="") as stream:
            expected = [(line, list(row.values())) for line, row in enumerate(csv.DictReader(stream), 2)]
        assert rows == expected and len(digest) == 64
        assert len(fields) == len(expected[0][1])


def test_ddl_quotes_identifiers_and_keeps_case_colliding_columns():
    spec = TABLES["workforce"]
    fields, rows, _ = loader.read_csv(DATA / spec.source_file, "workforce")
    ddl = [render(s) for s in loader.table_statements(spec, fields, {})]
    create = next(s for s in ddl if s.startswith("CREATE TABLE"))
    assert '"Vendor_Name" text NOT NULL' in create and '"VENDOR_NAME" text NOT NULL' in create
    assert create.startswith('CREATE TABLE "workforce"."ct_workforce_organization"')
    assert 'PRIMARY KEY ("Graph_Namespace", "Assignment_ID")' in create
    assert '"_source_line" integer NOT NULL UNIQUE' in create
    assert ddl[-1] == 'GRANT SELECT ON "workforce"."ct_workforce_organization" TO "citi_reader"'
    copy = render(loader.copy_statement(spec, fields))
    assert copy.endswith('"_source_line", "_source_file") FROM STDIN')


def test_no_row_values_are_built_into_sql(loaded):
    # Row values travel only through COPY; statements contain identifiers and fixed comment literals.
    sql_text = "\n".join(loaded.statements)
    assert "Aurelix Codeworks" not in sql_text and "ASN-001-002" not in sql_text


def test_comment_literals_are_escaped():
    statement = render(loader.table_statements(TABLES["master"], ["Graph_Namespace", "Vendor_ID"], {"Vendor_ID": "it's quoted"})[5])
    assert statement == 'COMMENT ON COLUMN "clm"."canonical_vendor_master"."Vendor_ID" IS \'it\'\'s quoted\''


def test_lineage_comments_include_categories_and_renames():
    comments = loader.lineage_comments(DATA)
    forecast = comments["CT_Vendor_Technology_Forecast.csv"]
    assert "renamed from ID" in forecast["record_id"]
    assert "remaining Sep-Dec forecast" in forecast["Col_2026_YTP"]
    assert "synthetic-only column" in comments["generated/canonical_vendor_master.csv"]["BCID"]
    assert loader.lineage_comments(Path("absent-directory")) == {}


@pytest.mark.parametrize("content,message", [
    ("a,b\n1\n", "Malformed row"), ("a,a\n1,2\n", "Invalid columns"), ("_source_line\n1\n", "Unsupported column"),
    ("x" * 64 + "\n1\n", "Unsupported column"), ("", "Invalid columns"),
])
def test_read_csv_rejects_unsupported_input(tmp_path, content, message):
    path = tmp_path / "input.csv"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(loader.LoadError, match=message):
        loader.read_csv(path, "master")


def test_init_creates_role_once_and_grants_select_only():
    conn = FakeConnection()
    loader.init(conn)
    loader.init(conn)
    assert sum(s.startswith("CREATE ROLE") for s in conn.statements) == 1
    grants = [s for s in conn.statements if s.startswith(("GRANT", "ALTER DEFAULT"))]
    assert grants and all("SELECT" in s or "USAGE" in s for s in grants)
    assert not any(re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|ALL)\b", s) for s in grants)


def test_load_is_idempotent_and_verify_passes(loaded):
    before = len(loaded.statements)
    report = loader.load(loaded, DATA)
    assert {r["action"] for r in report.values()} == {"unchanged"}
    assert not any(s.startswith(("DROP", "CREATE TABLE", "COPY")) for s in loaded.statements[before:])
    assert report["workforce"] == {"table": "workforce.ct_workforce_organization", "rows": 60, "action": "unchanged"}
    assert loader.verify(loaded, DATA)["valid"] is True
    forced = loader.load(loaded, DATA, force=True)
    assert {r["action"] for r in forced.values()} == {"loaded"}


def test_verify_detects_tampering_and_changed_source(loaded, tmp_path):
    loaded.tables[("finance", "ct_technology_financials")]["rows"][0][5] = "tampered"
    result = loader.verify(loaded, DATA)
    assert result["valid"] is False
    assert result["tables"]["financials"]["content_sha256_match"] is False
    assert result["tables"]["master"]["content_sha256_match"] is True
    copied = tmp_path / "data"
    shutil.copytree(DATA / "generated", copied / "generated")
    for relative in _FILES.values():
        shutil.copyfile(DATA / relative, copied / relative)
    with (copied / _FILES["organizations"]).open("a", encoding="utf-8") as stream:
        stream.write("\n")  # byte change without a content change
    assert loader.verify(loaded, copied)["tables"]["organizations"]["source_file_unchanged"] is False


def test_from_postgres_matches_csv_snapshot(loaded):
    from_csv = StructuredQueryService(DATA)
    from_pg = StructuredQueryService.from_postgres(READER, connect=lambda dsn: loaded)
    assert loaded.read_only is True
    assert from_pg._tables == from_csv._tables
    assert from_pg.get_budget_forecast_actual("V-005") == from_csv.get_budget_forecast_actual("V-005")
    assert from_pg.get_expiring_contracts(90) == from_csv.get_expiring_contracts(90)
    assert from_pg.get_vendor_workforce("V-001")["evidence"][0]["source_line"] == from_csv.get_vendor_workforce("V-001")["evidence"][0]["source_line"]


def test_from_postgres_keeps_bounds_and_required_columns(loaded):
    with pytest.raises(StructuredDataError, match="Row limit"):
        StructuredQueryService.from_postgres(READER, max_rows=10, connect=lambda dsn: loaded)
    del loaded.tables[("mdm", "organization_ou_crosswalk")]
    with pytest.raises(StructuredDataError, match="Invalid columns in organizations"):
        StructuredQueryService.from_postgres(READER, connect=lambda dsn: loaded)


def test_config_hides_secrets_and_requires_dsn():
    config = PostgresConfig.from_env({"CITI_PG_DSN": "host=h user=u password=secret-placeholder"})
    assert "secret-placeholder" not in repr(config)
    assert config.owner_dsn.endswith("secret-placeholder")
    with pytest.raises(PostgresConfigurationError, match="CITI_PG_READER_DSN is required"):
        config.reader_dsn
    with pytest.raises(PostgresConfigurationError, match="not a valid") as exc:
        PostgresConfig(owner="password='secret-placeholder").owner_dsn
    assert "secret-placeholder" not in str(exc.value)


def test_sanitized_error_hides_server_text():
    error = RuntimeError("password authentication failed for user secret-placeholder")
    error.sqlstate = "28P01"
    assert str(sanitized(error)) == "PostgreSQL operation failed (28P01)"
    assert "secret" not in str(sanitized(RuntimeError("secret-placeholder")))


def test_cli_runs_against_injected_connection(loaded, monkeypatch, capsys):
    monkeypatch.setenv("CITI_PG_DSN", "dbname=citi_vdi")
    assert cli.main(["verify", "--data-dir", str(DATA)], connect=lambda dsn, autocommit: loaded) == 0
    assert '"valid": true' in capsys.readouterr().out
    monkeypatch.delenv("CITI_PG_DSN")
    with pytest.raises(SystemExit) as exc:
        cli.main(["init"], connect=lambda dsn, autocommit: loaded)
    assert exc.value.code == 2
    assert "CITI_PG_DSN is required" in capsys.readouterr().err
