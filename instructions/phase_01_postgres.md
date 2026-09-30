# Phase 1: PostgreSQL as the structured source

> Index: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md). The invariants and
> protected results in index §3 apply to this phase.

**Goal:** load the structured business facts into a local PostgreSQL, with one
schema per simulated source system, and serve the existing deterministic
services from it with identical results.

**Prerequisites (user):** install PostgreSQL 17 for Windows locally. Create a
database `citi_vendor_360` and an owner login. Put the DSN in the environment, never in
a file.

## Tasks

1. Dependency: `uv add "psycopg[binary]>=3.2"`.
2. New `src/citi_project/services/postgres/`:
   - `config.py`: `PostgresConfig.from_env()` reads `CITI_PG_DSN` (owner role, for
     the loader) and `CITI_PG_READER_DSN` (read-only). Its repr and errors hide the
     secrets, following `knowledge_graph/config.py`.
   - `loader.py`: an idempotent load in a single transaction, one schema per
     simulated source system:

     | Schema | Simulates | Tables (from `structured_data/query_service.py:_FILES`) |
     |---|---|---|
     | `clm` | Icertis/Sirion | `canonical_vendor_master`, `business_case_tech_crosswalk` |
     | `finance` | Coupa/Ariba/SAP GL | `ct_technology_financials`, `ct_vendor_technology_forecast` |
     | `workforce` | Fieldglass/Beeline | `ct_workforce_organization` |
     | `mdm` | ERP master data | `organization_ou_crosswalk`, `vendor_external_id_crosswalk` |
     | `dependency` | CMDB | `contract_application_bridge` |

     - Columns keep their CSV names, quoted, and are stored as `text`, so
       decimal/date parsing stays identical in Python.
     - Quoted identifiers are case-sensitive, so `Vendor_Name` and `VENDOR_NAME` in
       `ct_workforce_organization` stay as two separate columns.
     - Each row gets `_source_line integer` and `_source_file text`, which keep
       evidence line citations.
     - Tables and columns get `COMMENT ON` text from
       `initial_plan/generated/schema_lineage.json` where it is available.
     - Create primary keys where the grain is unique (for example `record_id`,
       `Assignment_ID`).
     - Create the role `citi_reader` with `USAGE` and `SELECT` only.
   - `cli.py`: the `citi-pg` script, with subcommands:
     - `init`: schemas and roles
     - `load [--data-dir initial_plan]`
     - `verify`: row counts and SHA-256 checks against the CSVs
3. `StructuredQueryService.from_postgres(config)` is a classmethod. It reads each
   table as `citi_reader` into the same `self._tables[name]` shape `_load`
   produces (dicts of strings plus `_line`), keeps the namespace filter and the
   required-column checks, then calls the existing `_validate()`. The CSV
   constructor stays for offline tests.
4. `pyproject.toml`: add the `citi-pg` script and a `pg_integration` pytest marker.
   `tests/conftest.py` gets a `--pg-integration` option.

## Tests

- Offline: unit tests for the loader's SQL generation (identifiers quoted, no
  string-built values) and for `from_postgres` using a fake cursor.
- Opt-in `tests/integration/test_postgres.py`:
  - load, verify, and load again, which must change nothing
  - **parity**: every protected result in index §3 is identical from CSV and from
    Postgres
  - the `citi_reader` role cannot write

## Status

**Gate passed (2026-09-30)** on local PostgreSQL 17, database `citi_vendor_360`:
- `citi-pg init`, `load` (all 8 tables) and `verify` (`"valid": true`)
- `tests/integration/test_postgres.py --pg-integration`: 6 passed
- offline: 452 passed and 17 skipped

The DSNs are kept in the git-ignored `backend/.env`, which the CLIs load
(index §6). For the live pytest gates, pass it with `uv run --env-file .env`.

As built:
- `services/postgres/`:
  - `config.py`: DSNs are hidden from repr, and errors are sanitized to SQLSTATE only
  - `tables.py`: the schema/table/key layout
  - `loader.py`: `init`, `load` and `verify`
  - `reader.py`: used by `from_postgres`
  - `cli.py`: the `citi-pg` script
- SQL is composed only from `psycopg.sql.Identifier` and `Literal`. Row values
  travel only through `COPY`.
- `load` runs in one transaction. A table whose source file hash, row count and
  content digest all match the manifest (`citi_meta.load_manifest`) is left
  untouched, so a second load changes nothing. `--force` reloads every table.
- `verify` checks for each table:
  - the table is present
  - the column order matches
  - the row count matches
  - the SHA-256 digest of `(line, values)` matches, whether computed from the CSV
    or from the database
  - the source file is unchanged since the load
- Primary keys:
  - `Graph_Namespace` plus the natural key for every table except the
    application bridge, which has no unique business grain and uses
    `_source_line`
  - `_source_line` is unique in every table
- `StructuredQueryService.from_postgres(config)` uses the same column, bound and
  namespace checks (`_accept`) as the CSV path, then runs `_validate()`. The
  session is also set to read-only.

## Setup (user, once)

In `psql -U postgres`, as the superuser:

```sql
CREATE ROLE citi_owner LOGIN CREATEROLE;
\password citi_owner
CREATE DATABASE citi_vendor_360 OWNER citi_owner;
```

In a fresh PowerShell, set the owner DSN and create the schemas plus the
`citi_reader` group role (NOLOGIN):

```powershell
$p = Read-Host 'citi_owner password' -AsSecureString
$env:CITI_PG_DSN = "host=localhost port=5432 dbname=citi_vendor_360 user=citi_owner password=" + [System.Net.NetworkCredential]::new('', $p).Password; Remove-Variable p
uv run citi-pg init
```

Then create a login that inherits `citi_reader`, again in `psql -U citi_owner -d citi_vendor_360`:

```sql
CREATE ROLE citi_app LOGIN IN ROLE citi_reader;
\password citi_app
```

Set the reader DSN the same way:
`host=localhost port=5432 dbname=citi_vendor_360 user=citi_app password=...` goes in
`CITI_PG_READER_DSN`.

## Gate

`citi-pg verify` passes, the parity test passes and the offline suite is green:

```powershell
uv run citi-pg load; uv run citi-pg verify
.venv\Scripts\python.exe -B -m pytest tests/integration/test_postgres.py --pg-integration -q -rs -p no:cacheprovider
```

The integration test does the following:
- loads, verifies and loads again, and expects every table to be `unchanged`
- compares `_tables`, every protected result in index §3, and the per-vendor
  query methods between the CSV and PostgreSQL sources
- checks that `citi_app` gets `InsufficientPrivilege` on INSERT, DELETE, CREATE
  and DROP, even with the session read-only flag off
