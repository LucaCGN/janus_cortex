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

Repository primitives:
- module: `app/data/databases/repositories/upsert_primitives.py`
- class: `JanusUpsertRepository`

Seed packs:
- module: `app/data/databases/seed_packs/polymarket_event_seed_pack.py`
- default probes: `extra_3_7_past_nba_game_period`, `extra_3_8_upcoming_nba_availability`, `extra_3_9_aliens_grid_history`

Current implemented migrations:
- `0001_v0_3_1__catalog_core_mvp.sql`
- `0002_v0_3_2__sync_and_raw_payloads.sql`
- `0003_v0_3_3__portfolio_core_tables.sql`
- `0004_v0_3_4__market_data_append_only.sql`

## Tests
- run migration integration tests against real Postgres:
  - `$env:JANUS_RUN_DB_TESTS='1'; python -m pytest -q tests/app/data/databases/test_postgres_migrations_pytest.py`
- run repository/upsert primitive tests against real Postgres:
  - `$env:JANUS_RUN_DB_TESTS='1'; python -m pytest -q tests/app/data/databases/test_upsert_primitives_pytest.py`
- run live seed-pack integration tests against real Postgres + live APIs:
  - `$env:JANUS_RUN_DB_TESTS='1'; $env:JANUS_RUN_LIVE_TESTS='1'; python -m pytest -q tests/app/data/databases/test_polymarket_event_seed_pack_live_pytest.py`
- execute the live seed pack script directly:
  - `python -m app.data.databases.seed_packs.polymarket_event_seed_pack`

Notes:
- Tests marked `postgres_live` are skipped by default unless `JANUS_RUN_DB_TESTS=1`.
- Tests marked `live_api` are skipped unless `JANUS_RUN_LIVE_TESTS=1`.
- The migration test module drops managed schemas before execution to guarantee a clean baseline.
