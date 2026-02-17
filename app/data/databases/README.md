# Database Migrations

This folder contains Postgres connection helpers and SQL migrations.

## Environment
Required variables (from `.env`):
- `JANUS_POSTGRES_HOST`
- `JANUS_POSTGRES_PORT`
- `JANUS_POSTGRES_DB`
- `JANUS_POSTGRES_USER`
- `JANUS_POSTGRES_PASSWORD`

Optional:
- `JANUS_POSTGRES_CONNECT_TIMEOUT` (default `10`)
- `JANUS_POSTGRES_SSLMODE`

## Commands
- list migrations: `python -m app.data.databases.migrate --list`
- apply pending migrations: `python -m app.data.databases.migrate`
- apply up to one migration: `python -m app.data.databases.migrate --to 0002_v0_3_2__sync_and_raw_payloads.sql`

## Tests
- run migration integration tests against real Postgres:
  - `$env:JANUS_RUN_DB_TESTS='1'; python -m pytest -q tests/app/data/databases/test_postgres_migrations_pytest.py`

Notes:
- Tests marked `postgres_live` are skipped by default unless `JANUS_RUN_DB_TESTS=1`.
- The migration test module drops managed schemas before execution to guarantee a clean baseline.
