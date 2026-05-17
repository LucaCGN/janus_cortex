# Janus Cortex

Backend-first local prediction-market service for production-grade ingestion, market watching, strategy-plan execution, order reconciliation, replay capture, and continuous Codex-assisted development.

## Operating Boundary

Janus must run independently from Codex. The backend service owns ingestion, watch sessions, active `StrategyPlanJSON` loading, trigger evaluation, order-intent validation, audited order execution, portfolio reconciliation, and replay capture.

Codex automations, or an equivalent external agent framework, are required for the CI/CD and research operating loop: postgame review, development passes, pregame integrity checks, external research, live health monitoring, prompt refinement, and documentation discipline. Codex agents may submit research, context, and structured strategy-plan trigger revisions, but the local Janus engine remains the source of execution truth. Order sizing and portfolio exposure are operator policy, not pregame research output.

Live game execution is owned by the service-owned live strategy worker exposed through `/v1/ops/live-strategy-worker/*`. Codex tools can inspect, start, stop, or trigger one bounded worker tick, but Janus must provide the recurring heartbeat and strategy evaluation loop during active games.

## Current Status
- Current product direction: build Janus into a fully autonomous and self-improving expectation-markets trading system, starting with NBA/WNBA basketball because that is the most mature domain.
- Current integration branch: `main`. During the source-of-truth and CI/CD redesign phase, local progress is reconciled on `main`; new feature branches are not the active operating model unless explicitly reintroduced.
- Local runtime root: `JANUS_LOCAL_ROOT`, defaulting to `C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex\local`.
- Current source-of-truth workspace: `app\docs\planning\current\final_system\`.
- Current Obsidian vault: `C:\Users\lnoni\OneDrive\Documentos\Janus\Janus-Brain`.
- Current GitHub backlog: canonical issues `#17` through `#29` in `LucaCGN/janus_cortex`.
- Current live posture: live money and live LLM dispatch remain blocked until runtime cost/shutdown controls, ledger/reportability, and direct CLOB inventory gates are safe.
- Current API posture: the local Janus API may be intentionally down after the LLM cost incident; restore it only after a direct CLOB risk snapshot when integrity, passive capture, or same-day readiness work requires it.
- Current priority: finish the source-of-truth/Obsidian/GitHub/controller bootstrap, then execute issue `#17` for LLM runtime cost budgets, trigger dedup, model-call caps, cost telemetry, and final/flat shutdown.

## Scope Definitions
- `Phase 0`: stabilize Janus as a safe backend-first trading runtime. This includes direct CLOB authority, current-event inventory, LLM cost/shutdown safety, account ledger truth, event review bundles, manual/Codex intervention reconciliation, source-of-truth docs, Obsidian memory, and GitHub issue-backed CI/CD.
- `Phase 1`: make basketball live trading reproducible without constant master-chat intervention. NBA and WNBA share basketball event/PBP/orderbook/replay/report contracts, with league-specific calibration.
- `Phase 2`: promote deterministic, ML, and LLM lanes into coordinated scenario-bound strategy sleeves with profit-ratcheted risk management.
- `Phase 3`: expand from basketball into broader expectation-market infrastructure, including crypto, geopolitics, economics, elections, culture, and long-term portfolio monitoring only after core CLOB, ledger, risk, replay, and review systems are stable.

## Target Stack
- Postgres as the primary relational store
- FastAPI as the data and orchestration API
- Direct Polymarket CLOB as execution and portfolio truth
- Modular-monolith app services first, with multiple workers or services only when operationally necessary
- `StrategyPlanJSON` as the executable strategy contract after validation
- Service-owned live strategy worker for recurring evaluation
- Deterministic and ML lanes for fast repeatable interpretation
- Internal LLM for structured plan revisions and reconciliation actions after cost controls are in place
- Codex as controller, developer, reviewer, live analyst, and fallback strategy crafter
- GitHub issues for durable backlog and acceptance criteria
- Obsidian as an LLM-maintained second brain, not live truth

## Architecture Direction
The project uses a provider/category/module split:
- `app/providers/*`: upstream connectors such as Polymarket, NBA, and HoopsStats
- `app/domain/events/*`: canonical contracts plus category-aware event logic
- `app/ingestion/*`: canonical mapping and ingestion pipelines
- `app/modules/*`: module-level serving and orchestration surfaces
- Legacy compatibility paths remain under `app/data/*` while migration completes

## Roadmap Snapshot

### Immediate P0
1. `#23`: finish and commit the repo/Obsidian/GitHub/queue source-of-truth bootstrap.
2. `#17`: add LLM runtime cost budgets, trigger dedup, model-call caps, cost telemetry, and final/flat shutdown.
3. `#18`: build the event review bundle endpoint and decision timeline.
4. `#19`: repair account-scoped fill ledger and lifecycle attribution.
5. `#20`: include current-event inventory in every review and revision.
6. `#21`: add safe LLM/Codex strategy adoption and fallback flow.
7. `#22`: build a direct CLOB manual order assistant.

### Next P1
1. `#24`: basketball regime and scenario classifier.
2. `#25`: quarter and PBP price-impact feature lane.
3. `#26`: strategy sleeve generation and dependency graph.
4. `#27`: profit-ratcheted risk manager.
5. `#28`: close-game virtual-dead policy.
6. `#29`: WNBA minimal live-readiness track.

### Deferred Domains
Crypto, geopolitics, economics, elections, culture, and global portfolio management remain future modules. They should reuse shared CLOB, ledger, replay, risk, and reporting foundations instead of forking the basketball-specific runtime.

## Key Planning Docs
- `app/docs/planning/current/final_system/README.md`
- `app/docs/planning/current/final_system/premise_decisions_2026-05-17.md`
- `app/docs/planning/current/final_system/automation/master_controller_contract.md`
- `app/docs/planning/current/final_system/automation/task_queue_schema.md`
- `app/docs/planning/current/final_system/automation/docs_memory_health_check.md`
- `app/docs/planning/current/final_system/backlog/immediate_issue_seed_2026-05-17.md`
- `app/docs/planning/current/final_system/obsidian/bootstrap_map.md`
- `app/docs/planning/current/janus_final_system_premise_register.md`
- `app/docs/planning/codex_agents/shared_file_communication_contract.md`
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
- Basketball-core safety, CLOB truth, ledger attribution, and event review tooling come before broader crypto/geopolitics strategy branches.
- Strategy development is active but must flow through tested families, structured StrategyPlanJSON, direct CLOB reconciliation, risk sleeves, and replay/live-validation evidence.
- The frontend is not the production operating surface. Backend API endpoints, `codex_tool\`, tracked docs, and runtime handoffs are the operating interface.
