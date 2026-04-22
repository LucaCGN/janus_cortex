# Janus Cortex

Personal prediction-market data framework focused on production-grade data structures, ingestion pipelines, and service layers before strategy automation.

## Current Status
- Active analysis baseline: `v1_0_1` with benchmark contract `v11`
- Local checkpoint ledger source of truth: `JANUS_LOCAL_ROOT\tracks\dev-checkpoint` with workspace default `C:\code-personal\janus-local\janus_cortex`
- Current priority: harden the offline NBA controller stack under adverse execution assumptions before any live automation.
- Current scope: safe DB validation, research-only backtesting, controller comparison, and read-only consumers.
- Current NBA analysis snapshot on `2026-04-22`:
  - regular-season research-ready corpus: `1198 / 1224`
  - postseason validation corpus: `20` games (`6` play-in + `14` playoffs), all research-ready
  - postseason state-panel rows: `23,118`
  - final evaluated options under the adverse postseason contract:
    - `winner_definition`
    - `master_strategy_router_v1`
    - `gpt-5.4 :: llm_hybrid_freedom_compact_v1`
    - `gpt-5.4-mini :: llm_hybrid_freedom_compact_v1`

## Scope Definitions
- `v0.8.*`: NBA regular-season data completion for 2025/26.
- `v0.9.*`: NBA playoff-specific module design, ingestion, serving, and season handoff.
- `v1.*`: sports-first roadmap built on the NBA module, then shared-sports hardening so WNBA 2026 and NBA 2026/27 can run on the same base.
- `v2.0.0`: feature-complete expansion into WNBA, crypto, geopolitical, and general-event modules.

## v1 Target Stack
- Postgres as the primary relational store
- FastAPI as the data and orchestration API
- Dockerized service runtime for DB, API, and supporting jobs
- Research-memory or Chroma-style services only after sports-core data stability is proven; they do not block NBA regular-season or playoff completion

## Architecture Direction
The project uses a provider/category/module split:
- `app/providers/*`: upstream connectors such as Polymarket, NBA, and HoopsStats
- `app/domain/events/*`: canonical contracts plus category-aware event logic
- `app/ingestion/*`: canonical mapping and ingestion pipelines
- `app/modules/*`: module-level serving and orchestration surfaces
- Legacy compatibility paths remain under `app/data/*` while migration completes

## Roadmap Snapshot

### Completed block
- `v0.1.1` to `v0.1.6`: node and method validation baseline
- `v0.2.1` to `v0.2.9`: canonical contracts, app-structure boundaries, pytest topology, and docs synchronization
- `v0.3.1` to `v0.3.6`: database MVP, migrations, upsert primitives, and seed-pack integration
- `v0.4.1` to `v0.4.6`: ingestion pipelines to schema (`sync_events`, `sync_markets`, `sync_portfolio`, `sync_postgres`, `sync_mappings`, `backfill_retry`)
- `v0.5.1` to `v0.5.6`: FastAPI core layer and OpenAPI lock
- `v0.6.1` to `v0.6.6`: market-data and portfolio service layer plus manual order validation
- `v0.7.1` to `v0.7.6`: NBA serving layer, live context, selected-game validation, and query tuning

### In progress
- `v0.8.1` to `v0.8.8`: regular-season feature persistence, bounded backfills, coverage auditing, serving routes, replayable refreshes, rollups, and QA are largely complete
- `v1.4.6`: postseason data coverage, adverse execution replay, and final 4-option showdown are now merged into the active analysis state

### Planned lanes
1. `v1.5.0` controller hardening under the `v11` adverse-execution contract
2. `v1.5.1` context and payout-policy models around the frozen controller
3. `v1.5.2` focused read-only review UI for the final controller and LLM override lane
4. `v1.5.x` season continuity work for the remaining playoffs/preseason path and WNBA bootstrap
5. `v2.0.0` multi-module expansion across WNBA, crypto, geopolitical, and general events

## Key Planning Docs
- `app/docs/reference/README.md`
- `app/docs/reference/current_analysis_system_state.md`
- `app/docs/reference/postseason_final_20_validation.md`
- `app/docs/planning/README.md`
- `app/docs/planning/current/roadmap_to_multi_algo_backtests.md`
- `app/docs/planning/current/nba_analysis_next_steps.md`
- `app/docs/development_guide.md`
- `app/docs/local_workspace_convention.md`
- `app/docs/scalable_db_schema_proposal.md`
- `app/docs/scalable_api_routes_proposal.md`
- `app/docs/source_temporal_coverage.md`
- `app/docs/app_structure_modularization_plan.md`

## Testing Rules
Pytest naming and discovery:
- file suffix: `*_pytest.py`
- mirrored test paths under top-level `tests/app/...`
- live external tests are gated by `JANUS_RUN_LIVE_TESTS=1`
- live Postgres integration tests are gated by `JANUS_RUN_DB_TESTS=1`

Common commands:
- `python -m pytest -q`
- `$env:JANUS_RUN_LIVE_TESTS='1'; python -m pytest -q tests/app/data/nodes/test_temporal_coverage_live_pytest.py`
- `$env:JANUS_RUN_DB_TESTS='1'; python -m pytest -q tests/app/data/databases/test_postgres_migrations_pytest.py`
- `python -m app.data.databases.migrate`
- `python -m app.data.pipelines.daily.nba.season_strategy_audit --season 2025-26 --pbp-max-workers 8 --moneyline-window-days 14 --moneyline-max-pages 30 --history-sample-events-per-month 3`
- `python -m app.data.pipelines.daily.nba.sync_postgres --season 2025-26 --schedule-window-days 2`
- `python -m app.data.pipelines.daily.polymarket.sync_events --probe-set today_nba --max-finished 1 --max-live 1`
- `python -m app.data.pipelines.daily.polymarket.sync_markets --probe-set today_nba --max-finished 2 --max-live 2 --include-upcoming`
- `python -m app.data.pipelines.daily.polymarket.backfill_retry --max-finished 2 --max-live 2 --include-upcoming --candle-timeframe 1m --candle-lookback-hours 48`
- `uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --reload`
- `$env:JANUS_RUN_DB_TESTS='1'; python -m pytest -q tests/app/api`

## Notes
- Local checkpoint and reference material should live under `JANUS_LOCAL_ROOT` rather than the repository root.
- Current execution planning belongs under `app/docs/planning/current`; closed execution waves belong under `app/docs/planning/archive`.
- Use `powershell -ExecutionPolicy Bypass -File .\tools\janus_local.ps1 status` at the start of a session when preparing parallel work.
- Sports-core data completeness comes before Chroma, LLM memory, or broader multi-module expansion.
- Strategy logic is now frozen around the offline NBA analysis controller stack; the current goal is execution hardening and review tooling, not new family proliferation.
