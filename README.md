# Janus Cortex

Backend-first local prediction-market service for production-grade ingestion, market watching, strategy-plan execution, order reconciliation, replay capture, and continuous Codex-assisted development.

## Operating Boundary

Janus must run independently from Codex. The backend service owns ingestion, watch sessions, active `StrategyPlanJSON` loading, trigger evaluation, order-intent validation, audited order execution, portfolio reconciliation, and replay capture.

Codex automations, or an equivalent external agent framework, are required for the CI/CD and research operating loop: postgame review, development passes, pregame integrity checks, external research, live health monitoring, prompt refinement, and documentation discipline. Codex agents may submit research and structured strategy-plan revisions, but the local Janus engine remains the source of execution truth.

## Current Status
- Active analysis baseline: `v1_0_1` with the locked controller-vNext playoff contract
- Local runtime root: `JANUS_LOCAL_ROOT`, defaulting to `C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex\local`
- Agentic backend operating plan: `app\docs\planning\janus_agentic_backend_operating_plan.md`
- Codex agent automation prompts: `app\docs\planning\codex_agent_automation_prompts.md`
- Codex automation tools live under `codex_tool\`
- Current priority: keep the agentic backend operating loop live for the NBA playoff slate while hardening direct CLOB reconciliation, StrategyPlanJSON execution, watch-session replay, and minimum-size live testing.
- Current scope: backend ops endpoints, Codex tools, live pregame/postgame workflow, strategy-plan validation/execution, stop/hedge/order-policy testing, decision logging, and unified benchmark control across replay, ML, LLM, and live-validation lanes.
- Current NBA analysis snapshot on `2026-04-23`:
  - regular-season research-ready corpus: `1198 / 1224`
  - postseason validation corpus: `22` games (`6` play-in + `16` playoffs), all research-ready
  - locked primary controller: `controller_vnext_unified_v1 :: balanced`
  - locked no-LLM fallback: `controller_vnext_deterministic_v1 :: tight`
  - hostile replay contract:
    - target exposure `80%`
    - base floor `20%`
    - max concurrent positions `5`
    - random adverse slippage `0-5c`
  - full regular-season lock check:
    - primary controller median end bankroll: `$469,835.30`
    - deterministic fallback median end bankroll: `$68,486.79`
  - postseason reference:
    - primary controller median end bankroll: `$13.81`
    - deterministic fallback median end bankroll: `$14.33`
  - live executor status:
    - local live executor v1 mounted at `/live-control`
    - run launcher available at `tools/start_live_run.py`
    - controller core remains frozen; only execution profile versions iterate (`v1`, `v2`, ...)
- Current benchmark integration snapshot on `2026-04-24`:
  - replay-engine shared contract now published under `JANUS_LOCAL_ROOT\shared\benchmark_contract\replay_contract_current.md`
  - unified comparison dashboard now mounted at `/analysis-studio`
  - shared export command now available at `python tools/export_benchmark_dashboard.py`
  - replay is now the realism baseline; standard backtest, replay result, and live observed remain separate result views
  - current compare-ready lanes are `locked-baselines`, `replay-engine-hf`, `ml-trading`, and `llm-strategy`
  - current live-ready stack is still only the locked controller pair: `controller_vnext_unified_v1 :: balanced` and `controller_vnext_deterministic_v1 :: tight`
  - current live-probe tier is `quarter_open_reprice` plus `micro_momentum_continuation`, but today they still execute as shadow because live executor v1 cannot route standalone probes
  - current replay shadow set includes `inversion` and `lead_fragility`, while replay bench-only families remain visible but not promotable
  - ML v2 is compare-ready as context, ranking, calibration, and confidence metadata for strategy-plan selection; it does not yet have standalone execution authority
  - LLM strategy authority now flows through structured `StrategyPlanJSON`: the LLM may choose and combine executable strategy families, while the order manager enforces mechanical safety

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
- `v1.4.6` to `v1.4.7`: postseason coverage, adverse execution replay, and controller-vNext hardening are merged into the active analysis state

### Planned lanes
1. `v1.5.0` local live playoff validation loop for the locked controller pair
2. `v1.5.1` decision logging and ML-ready candidate dataset contract
3. `v1.5.2` focused read-only review UI for the locked controller and fallback
4. `v1.5.x` season continuity work for the remaining playoffs/preseason path and WNBA bootstrap
5. `v2.0.0` multi-module expansion across WNBA, crypto, geopolitical, and general events

## Key Planning Docs
- `app/docs/reference/README.md`
- `app/docs/reference/current_analysis_system_state.md`
- `app/docs/reference/controller_vnext_final_tuning.md`
- `app/docs/reference/unified_benchmark_contract.md`
- `app/docs/reference/live_playoff_validation_runbook.md`
- `app/docs/reference/postseason_final_20_validation.md`
- `app/docs/planning/README.md`
- `app/docs/planning/current/roadmap_to_multi_algo_backtests.md`
- `app/docs/planning/current/benchmark_integration_roadmap.md`
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
- `python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8010`
- `python tools/start_live_run.py --api-root http://127.0.0.1:8010 --run-id live-2026-04-23-v1 --game-id 0042500123 --game-id 0042500133 --game-id 0042500163 --dry-run`
- `$env:JANUS_RUN_DB_TESTS='1'; python -m pytest -q tests/app/api`

## Notes
- Local checkpoint and reference material should live under `JANUS_LOCAL_ROOT` rather than the repository root.
- Current execution planning belongs under `app/docs/planning/current`; closed execution waves belong under `app/docs/planning/archive`.
- Use `powershell -ExecutionPolicy Bypass -File .\tools\janus_local.ps1 status` at the start of a session when preparing parallel work.
- Sports-core data completeness and the generic watch/replay foundation come before broader crypto/geopolitics strategy branches.
- Strategy development is active but must flow through tested families, structured StrategyPlanJSON, direct CLOB reconciliation, and replay/live-validation evidence.
- The frontend is not the production operating surface. Backend API endpoints, `codex_tool\`, tracked docs, and runtime handoffs are the operating interface.
