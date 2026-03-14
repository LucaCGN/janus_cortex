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
- dynamic probes (`v0.4.1`): built from NBA live scoreboard via `build_today_nba_event_probes_from_scoreboard` (`finished`, `live`, optional `upcoming`)

Current implemented migrations:
- `0001_v0_3_1__catalog_core_mvp.sql`
- `0002_v0_3_2__sync_and_raw_payloads.sql`
- `0003_v0_3_3__portfolio_core_tables.sql`
- `0004_v0_3_4__market_data_append_only.sql`
- `0005_v0_4_2__catalog_market_state_snapshots.sql`
- `0006_v0_4_3__portfolio_order_events.sql`
- `0007_v0_4_4__nba_ingestion_tables.sql`
- `0008_v0_4_5__catalog_event_information_scores.sql`
- `0009_v0_4_6__market_data_outcome_price_candles.sql`
- `0010_v0_5_1__ops_core_tables.sql`
- `0011_v0_6_2__portfolio_valuation_snapshots.sql`

## Tests
- run migration integration tests against real Postgres:
  - `$env:JANUS_RUN_DB_TESTS='1'; python -m pytest -q tests/app/data/databases/test_postgres_migrations_pytest.py`
- run repository/upsert primitive tests against real Postgres:
  - `$env:JANUS_RUN_DB_TESTS='1'; python -m pytest -q tests/app/data/databases/test_upsert_primitives_pytest.py`
- run live seed-pack integration tests against real Postgres + live APIs:
  - `$env:JANUS_RUN_DB_TESTS='1'; $env:JANUS_RUN_LIVE_TESTS='1'; python -m pytest -q tests/app/data/databases/test_polymarket_event_seed_pack_live_pytest.py`
- execute the live seed pack script directly:
  - `python -m app.data.databases.seed_packs.polymarket_event_seed_pack`
- run `v0.4.1` polymarket daily ingestion probe pipeline:
  - `python -m app.data.pipelines.daily.polymarket.sync_events --probe-set today_nba --max-finished 1 --max-live 1`
  - wrapper alias: `python -m app.ingestion.pipelines.prediction_market_polymarket.sync_events --probe-set today_nba`
- run `v0.4.2` markets/outcomes + state snapshots pipeline:
  - `python -m app.data.pipelines.daily.polymarket.sync_markets --probe-set today_nba --max-finished 2 --max-live 2 --include-upcoming`
  - wrapper alias: `python -m app.ingestion.pipelines.prediction_market_polymarket.sync_markets --probe-set today_nba`
- run `v0.4.3` portfolio mirror pipeline:
  - `python -m app.data.pipelines.daily.polymarket.sync_portfolio --wallet <0x_wallet>`
  - wrapper alias: `python -m app.ingestion.pipelines.prediction_market_polymarket.sync_portfolio --wallet <0x_wallet>`
- run `v0.4.4` NBA metadata/live postgres pipeline:
  - `python -m app.data.pipelines.daily.nba.sync_postgres --season 2025-26 --schedule-window-days 2`
  - wrapper alias: `python -m app.ingestion.pipelines.sports_nba.sync_postgres --season 2025-26 --schedule-window-days 2`
- run `v0.4.5` cross-domain mapping pipeline:
  - `python -m app.data.pipelines.daily.cross_domain.sync_mappings --lookback-days 3 --lookahead-days 2`
  - wrapper alias: `python -m app.ingestion.pipelines.cross_domain.sync_mappings --lookback-days 3 --lookahead-days 2`
- run `v0.4.6` backfill/retry + candle aggregation pipeline:
  - `python -m app.data.pipelines.daily.polymarket.backfill_retry --max-finished 2 --max-live 2 --include-upcoming --candle-timeframe 1m --candle-lookback-hours 48`
  - wrapper alias: `python -m app.ingestion.pipelines.prediction_market_polymarket.backfill_retry --max-finished 2 --max-live 2 --include-upcoming --candle-timeframe 1m --candle-lookback-hours 48`
- run `v0.4.1` live DB/API integration tests (today finished + live stream capture):
  - `$env:JANUS_RUN_DB_TESTS='1'; $env:JANUS_RUN_LIVE_TESTS='1'; python -m pytest -q tests/app/data/databases/test_polymarket_event_seed_pack_v0_4_live_pytest.py`
- run `v0.4` DB pipeline integration tests:
  - `$env:JANUS_RUN_DB_TESTS='1'; python -m pytest -q tests/app/data/pipelines/daily/polymarket/test_sync_portfolio_pytest.py tests/app/data/pipelines/daily/nba/test_sync_postgres_pytest.py tests/app/data/pipelines/daily/cross_domain/test_sync_mappings_pytest.py tests/app/data/pipelines/daily/polymarket/test_backfill_retry_pytest.py`
- run `v0.4` live pipeline integration tests:
  - `$env:JANUS_RUN_DB_TESTS='1'; $env:JANUS_RUN_LIVE_TESTS='1'; python -m pytest -q tests/app/data/pipelines/daily/nba/test_sync_postgres_live_pytest.py tests/app/data/pipelines/daily/polymarket/test_backfill_retry_live_pytest.py`
- run API + DB validations (`v0.5`):
  - `$env:JANUS_RUN_DB_TESTS='1'; python -m pytest -q tests/app/data/databases/test_postgres_migrations_pytest.py tests/app/data/databases/test_upsert_primitives_pytest.py tests/app/api`
- run API + DB validations (`v0.6`):
  - `$env:JANUS_RUN_DB_TESTS='1'; python -m pytest -q tests/app/api`
- run live endpoint validations (`v0.5`):
  - `$env:JANUS_RUN_DB_TESTS='1'; $env:JANUS_RUN_LIVE_TESTS='1'; python -m pytest -q tests/app/api/test_live_today_games_endpoints_pytest.py`
- run FastAPI service for manual checks:
  - `uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --reload`

Notes:
- Tests marked `postgres_live` are skipped by default unless `JANUS_RUN_DB_TESTS=1`.
- Tests marked `live_api` are skipped unless `JANUS_RUN_LIVE_TESTS=1`.
- The migration test module drops managed schemas before execution to guarantee a clean baseline.
