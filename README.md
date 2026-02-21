# Janus Cortex

Personal prediction-market data framework focused on production-grade data structures and service layers before strategy automation.

## Current Status
- Active checkpoint: `v0.5.1` (FastAPI skeleton and dependency wiring)
- Checkpoint ledger source of truth: `dev-checkpoint/README.md`
- Current scope: schema + ingestion + API readiness, not autonomous strategy execution.

## v1 Target Stack
- Postgres (primary relational store)
- ChromaDB (event-document collections)
- FastAPI (service and orchestration API)
- Dockerized multi-service runtime

## Architecture Direction
The project now uses a provider/category/module split:
- `app/providers/*`: upstream connectors (Polymarket, NBA, HoopsStats, JinaAI)
- `app/domain/events/*`: canonical contracts + category-aware event domain logic
- `app/ingestion/*`: canonical mapping + ingestion pipelines
- `app/modules/*`: module-level serving/orchestration surfaces
- Legacy compatibility paths remain under `app/data/*` while migration completes.

## Roadmap (from `dev-checkpoint/README.md`)

### Completed block
- `v0.1.1` - `v0.1.6`: node and method validation baseline
- `v0.2.1` - `v0.2.6`: canonical contracts, adapters, ID rules, scoring, quality gates, integration packs
- `v0.2.7` - `v0.2.9`: app structure refactor gate, pytest topology hardening, docs synchronization
- `v0.3.1` - `v0.3.6`: database MVP, migrations, upsert primitives, seed-pack integration
- `v0.4.1` - `v0.4.6`: ingestion pipelines to schema (`sync_events`, `sync_markets`, `sync_portfolio`, `sync_postgres`, `sync_mappings`, `backfill_retry`)

### In progress
- `v0.5.1`: FastAPI skeleton and dependency wiring

### Planned lanes
1. `v0.5.*` FastAPI core layer
2. `v0.6.*` Portfolio and market-data service layer
3. `v0.7.*` NBA module serving layer
4. `v0.8.*` Chroma event-document layer
5. `v0.9.*` Production hardening and v1 cut

## Key Planning Docs
- `app/docs/development_guide.md`
- `app/docs/scalable_db_schema_proposal.md`
- `app/docs/scalable_api_routes_proposal.md`
- `app/docs/source_temporal_coverage.md`
- `app/docs/app_structure_modularization_plan.md`
- `dev-checkpoint/README.md`

## Testing
Pytest naming/discovery:
- file suffix: `*_pytest.py`
- mirrored test paths under top-level `tests/app/...`
- live external tests are gated by `JANUS_RUN_LIVE_TESTS=1`

Common commands:
- `python -m pytest -q`
- `$env:JANUS_RUN_LIVE_TESTS='1'; python -m pytest -q tests/app/data/nodes/test_temporal_coverage_live_pytest.py`
- `python -m app.data.databases.migrate`
- `$env:JANUS_RUN_DB_TESTS='1'; python -m pytest -q tests/app/data/databases/test_postgres_migrations_pytest.py`
- `$env:JANUS_RUN_DB_TESTS='1'; python -m pytest -q tests/app/data/databases/test_upsert_primitives_pytest.py`
- `$env:JANUS_RUN_DB_TESTS='1'; $env:JANUS_RUN_LIVE_TESTS='1'; python -m pytest -q tests/app/data/databases/test_polymarket_event_seed_pack_live_pytest.py`
- `$env:JANUS_RUN_DB_TESTS='1'; $env:JANUS_RUN_LIVE_TESTS='1'; python -m pytest -q tests/app/data/databases/test_polymarket_event_seed_pack_v0_4_live_pytest.py`
- `python -m app.data.databases.seed_packs.polymarket_event_seed_pack`
- `python -m app.data.pipelines.daily.polymarket.sync_events --probe-set today_nba --max-finished 1 --max-live 1`
- `python -m app.data.pipelines.daily.polymarket.sync_markets --probe-set today_nba --max-finished 2 --max-live 2 --include-upcoming`
- `python -m app.data.pipelines.daily.polymarket.sync_portfolio --wallet <0x_wallet>`
- `python -m app.data.pipelines.daily.nba.sync_postgres --season 2025-26 --schedule-window-days 2`
- `python -m app.data.pipelines.daily.cross_domain.sync_mappings --lookback-days 3 --lookahead-days 2`
- `python -m app.data.pipelines.daily.polymarket.backfill_retry --max-finished 2 --max-live 2 --include-upcoming --candle-timeframe 1m --candle-lookback-hours 48`

## Notes
- `dev-checkpoint/*` is a session execution ledger and may be gitignored depending on local policy.
- Strategy logic is intentionally not part of core v1 readiness scope until data contracts/services are stable.
