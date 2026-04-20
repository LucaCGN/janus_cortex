# Database Safety Workflow

## Purpose
Define the database safety ladder that every agent and session must follow before running migrations, DB integration tests, or heavy analysis commands.

This is the canonical reference for:
- target classification
- disposable Postgres workflow
- dev-clone workflow
- what is safe to run in each stage

## Target Classes

| Target | Meaning | Safe For `JANUS_RUN_DB_TESTS=1` | Typical Use |
| --- | --- | --- | --- |
| `disposable` | local Docker Postgres created from migrations only | yes | migration smoke, repository integration tests, seeded analysis tests |
| `dev_clone` | non-live copy of shared data | yes | realistic validation, count reconciliation, performance checks |
| `shared_live` | shared live or production-like database | no by default | only after disposable and dev-clone validation |
| `default` | unclassified target | no by default | legacy fallback until explicitly classified |

Classification env:
- `JANUS_DB_TARGET`
- optional legacy alias: `JANUS_POSTGRES_TARGET`

## Safety Enforcement
- `postgres_live` pytest modules only run when:
  - `JANUS_RUN_DB_TESTS=1`
  - and `JANUS_DB_TARGET` is `disposable` or `dev_clone`
- the only override is `JANUS_ALLOW_UNSAFE_DB_TESTS=1`
- use the override only for an explicit operator decision, never as the default path

## Safety Ladder
1. fixture, DataFrame, or SQLite-compatible validation
2. disposable local Postgres from migrations only
3. dev clone of live data
4. shared live database

The important rule is simple:
- never point `JANUS_RUN_DB_TESTS=1` or destructive reset commands at the shared live database first

## Disposable Postgres Workflow
Use [tools/janus_db.ps1](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/tools/janus_db.ps1).

Common commands:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\janus_db.ps1 status
powershell -ExecutionPolicy Bypass -File .\tools\janus_db.ps1 bootstrap-disposable
powershell -ExecutionPolicy Bypass -File .\tools\janus_db.ps1 reset-disposable
powershell -ExecutionPolicy Bypass -File .\tools\janus_db.ps1 smoke-disposable
powershell -ExecutionPolicy Bypass -File .\tools\janus_db.ps1 teardown-disposable
```

What the helper does:
- runs a local Docker Postgres container on `127.0.0.1:55432`
- sets a disposable target classification for child Python commands
- applies the full migration chain
- writes a reusable env snapshot to `JANUS_LOCAL_ROOT\tracks\reference\db\disposable_postgres.env`

## Migration Commands
The migration CLI now supports target introspection and safe reset flow:

```powershell
python -m app.data.databases.migrate --list
python -m app.data.databases.migrate --describe-target
python -m app.data.databases.migrate --drop-managed-schemas --require-safe-target
```

Use `--drop-managed-schemas` only on `disposable` or `dev_clone`.

## Dev-Clone Workflow
Use `dev_clone` after disposable validation passes.

Required steps:
1. confirm the clone is not the shared live database
2. set `JANUS_DB_TARGET=dev_clone`
3. run migration smoke and the targeted integration tests
4. run the analysis validation checklist
5. only after that, consider any shared-db command

Required preflight questions:
- where was the clone restored from
- when was it last refreshed
- what rollback or rebuild path exists
- what commands are allowed against it

## Validation Split
- disposable target proves migration shape, repository primitives, and seeded analysis flows
- dev clone proves realistic data counts, runtime characteristics, and baseline reconciliation
- shared live should only be used for approved operational runs after both lower stages pass
