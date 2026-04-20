# Janus Cortex Development Guide (Persistent Session Handoff)

## Purpose
This guide keeps continuity across sessions and agents.
It defines:
- project context,
- current phase,
- how to execute work,
- how to update structure and docs,
- mandatory synchronization between checkpoints, schema, and routes.

## Project Context
Janus Cortex is still in data-platform mode.
The immediate priority is NBA regular-season data completeness before the 2025/26 playoffs start.

Sports-first v1 runtime target:
- Postgres as the primary relational store
- FastAPI as the data and orchestration API
- Dockerized services for DB, API, and supporting jobs
- research-memory or Chroma-style services only after sports-core stability is proven; they are not blockers for `v0.8.*` or `v0.9.*`

v1 scope:
- stable NBA regular-season dataset and serving layer
- playoff-specific NBA module and postseason handoff
- manual dev scripts and operator-triggered jobs to validate behavior
- shared-sports preparation so WNBA 2026 and NBA 2026/27 can run on the same base by later `v1.*`
- no autonomous strategy execution inside the core app

v2 scope:
- feature-complete WNBA module
- crypto, geopolitical, and general-event modules
- generalized research-memory layer across categories

## Current Phase
- Active phase: `v0.8.1` with explicit parallel implementation work across `v0.8.2` to `v0.8.8`
- Reference source of truth: local checkpoint ledger under `JANUS_LOCAL_ROOT\tracks\dev-checkpoint`
- Latest completed block: `v0.7.1` to `v0.7.6` closed on `2026-03-14`
- Pre-`v0.8.1` gate completed on `2026-03-14`: full-season `2025-26` strategy-data audit confirmed full finished-game play-by-play coverage and partial season-wide Polymarket odds coverage.
- Live `v0.8` implementation state on `2026-03-14`:
  - `nba.nba_game_feature_snapshots=52`
  - `nba.nba_odds_coverage_audits=53`
  - `nba.nba_team_feature_rollups=30`
  - FastAPI version `0.8.1` with season refresh plus regular-season feature routes active

## Roadmap Boundaries
- `v0.8.*`: NBA regular-season data completion
- `v0.9.*`: NBA playoff module and season handoff
- `v1.*`: sports-first stabilization, shared-sports bootstrap, and next-season live operation
- `v2.0.0`: multi-module expansion into WNBA, crypto, geopolitical, and general events

## Canonical Planning Files
1. `app/docs/scalable_db_schema_proposal.md`
2. `app/docs/scalable_api_routes_proposal.md`
3. local checkpoint ledger under `JANUS_LOCAL_ROOT\tracks\dev-checkpoint`
4. `app/docs/source_temporal_coverage.md`
5. `app/docs/development_guide.md` (this file)
6. `app/docs/local_workspace_convention.md`
7. `app/docs/app_structure_modularization_plan.md`
8. `app/docs/openapi_v0_8_snapshot.json`

## Structure Gate Reminder
The pre-`v0.3` structure gate is completed and documented in:
- `app/docs/app_structure_modularization_plan.md`

Core principle:
- providers (`app/providers/*`) and event categories (`app/domain/events/categories/*`) must stay independent axes.
- NBA is a category module, not the app root structure.

Completed structure checkpoints:
- `v0.2.7`: structure boundaries plus compatibility wrappers
- `v0.2.8`: pytest topology plus regression validation
- `v0.2.9`: doc and checkpoint synchronization

## Execution Model
1. Read the active checkpoint file first.
2. Implement only work in scope for the active phase.
3. Create or update pytest modules for every refactored node method in scope.
4. Run the tests listed in the checkpoint.
5. If a phase completes, update its status and advance the next dependency phase.
6. Never activate routes or tables before their dependency phase is complete.

## Required Update Protocol

### A. If schema changed
Update all:
- `app/docs/scalable_db_schema_proposal.md`
- current checkpoint file
- local checkpoint ledger README under `JANUS_LOCAL_ROOT\tracks\dev-checkpoint`
- `README.md` if the roadmap or scope definition changed

Also keep synchronized:
- table name
- column list
- activation phase
- migration id or script reference

### B. If route changed
Update all:
- `app/docs/scalable_api_routes_proposal.md`
- current checkpoint file
- route test references in the checkpoint

Also keep synchronized:
- method plus path
- activation phase
- required tables
- pytest module path

### C. If node method changed (`app/data/nodes/*`)
Update all:
- current checkpoint method inventory section
- local checkpoint ledger README under `JANUS_LOCAL_ROOT\tracks\dev-checkpoint` if the phase objective or dependency meaning changed
- test commands and expected outcomes in the checkpoint
- dedicated pytest modules for changed methods
- temporal coverage notes in `app/docs/source_temporal_coverage.md` when time-based methods are involved

## Node Method Testing Rule (Non-Negotiable)
For each node method refactor or new method:
1. Create or update a dedicated `pytest` module.
2. Cover positive path, malformed input, and source-unavailable fallback behavior.
3. Execute pytest in the same phase where method work happened.
4. Record commands and results in the active checkpoint.

Repository conventions:
- pytest file suffix: `*_pytest.py`
- place tests under top-level `tests/app/...` mirroring `app/...`
- do not rely on legacy manual scripts for pytest discovery

Execution conventions:
- default run: `python -m pytest -q`
- live source validation: `$env:JANUS_RUN_LIVE_TESTS='1'; python -m pytest -q <module_or_pattern>`
- Postgres integration validation: `$env:JANUS_RUN_DB_TESTS='1'; python -m pytest -q tests/app/data/databases/test_postgres_migrations_pytest.py`

## Temporal Coverage Validation Rule (Non-Negotiable)
For time-based source methods:
1. Validate and document past data coverage.
2. Validate and document current or live coverage.
3. Validate and document future or scheduled coverage.
4. Record limitations and failure modes.
5. Update `app/docs/source_temporal_coverage.md` in the same phase.

## Folder and Structure Rules

### Data Nodes First Rule
Before DB or API expansion, validate methods under:
- `app/data/nodes/polymarket/*`
- `app/data/nodes/nba/*`
- `app/data/nodes/hoopsstats/*`

If a source endpoint is unstable or unavailable:
- implement a fallback collector to persist streamed ticks or events,
- store to history tables for future analysis,
- mark source provenance explicitly in payload metadata.

### Checkpoint Discipline
- every checkpoint file has a status: `planned`, `in_progress`, `blocked`, or `done`
- every checkpoint file has explicit exit criteria
- do not skip checkpoint numbers
- do not delete prior checkpoint files
- use additional subphases freely when a block needs more granularity; phase depth is not capped at `.6`

## Local Workspace Rule
- keep branch-independent local state under `JANUS_LOCAL_ROOT`, not in the repository root
- default workspace local root: `C:\code-personal\janus-local\janus_cortex`
- use [tools/janus_local.ps1](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/tools/janus_local.ps1) to create the local layout, move ad hoc folders, export stash snapshots, and clear generated caches
- do not recreate repo-root `dev-checkpoint`, `reference`, or `output` folders unless they are temporary and immediately moved out again

## Definition of Done
A checkpoint is only `done` when:
1. all in-scope implementation tasks are complete,
2. all required tests passed,
3. artifacts listed in the checkpoint are updated,
4. evidence is written in the checkpoint result section,
5. the next dependency gate is clearly satisfied.

## Session Handoff Template
Use this structure in the active checkpoint at session end:
- `Date:`
- `Phase:`
- `What changed:`
- `Tests run:`
- `Pass/fail:`
- `Open blockers:`
- `Next exact action:`

## Collaboration Rule
- Ask the user whenever external input or a product decision is required.
- Notify the user on each subphase completion with:
  - what completed,
  - what changed in schema, routes, or planning,
  - what is next.

## Non-Negotiable Consistency Checks
Before closing any session:
1. active checkpoint status is accurate
2. schema doc table-phase-column relations reflect the latest plan
3. route doc activation matrix reflects the latest route state
4. if a phase completed, the next phase is marked `in_progress`
5. root `README.md` and the local checkpoint ledger README agree on roadmap scope

## Fast Reference Commands
- show local workspace status: `powershell -ExecutionPolicy Bypass -File .\tools\janus_local.ps1 status`
- ensure local workspace layout: `powershell -ExecutionPolicy Bypass -File .\tools\janus_local.ps1 ensure`
- list node files: `rg --files app/data/nodes`
- track schema mentions: `rg -n "activate:|table|column|phase" app/docs/scalable_db_schema_proposal.md`
- track routes mentions: `rg -n "v0\.|v1\.|v2\.|/v1/" app/docs/scalable_api_routes_proposal.md`
- apply migrations: `python -m app.data.databases.migrate`
- run API locally: `uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --reload`
- run API tests: `$env:JANUS_RUN_DB_TESTS='1'; python -m pytest -q tests/app/api`
- run live API validation: `$env:JANUS_RUN_DB_TESTS='1'; $env:JANUS_RUN_LIVE_TESTS='1'; python -m pytest -q tests/app/api/test_live_today_games_endpoints_pytest.py`
- run NBA Postgres sync: `python -m app.data.pipelines.daily.nba.sync_postgres --season 2025-26 --schedule-window-days 2`
- run NBA regular-season refresh: `python -m app.data.pipelines.daily.nba.regular_season_features --season 2025-26 --only-finished --max-games 50 --skip-odds-fetch`
- run season-wide strategy audit: `python -m app.data.pipelines.daily.nba.season_strategy_audit --season 2025-26 --pbp-max-workers 8 --moneyline-window-days 14 --moneyline-max-pages 30 --history-sample-events-per-month 3`
- run Polymarket event sync: `python -m app.data.pipelines.daily.polymarket.sync_events --probe-set today_nba --max-finished 1 --max-live 1`
- run Polymarket market sync: `python -m app.data.pipelines.daily.polymarket.sync_markets --probe-set today_nba --max-finished 2 --max-live 2 --include-upcoming`
- run Polymarket backfill and candle aggregation: `python -m app.data.pipelines.daily.polymarket.backfill_retry --max-finished 2 --max-live 2 --include-upcoming --candle-timeframe 1m --candle-lookback-hours 48`
- run portfolio mirror sync: `python -m app.data.pipelines.daily.polymarket.sync_portfolio --wallet <0x_wallet>`
- run closed-position consolidation: `python -m app.data.pipelines.daily.polymarket.consolidate_closed_positions --wallet <0x_wallet>`

## Governance Note
If requested work conflicts with the phase ordering, document the exception in the active checkpoint and record the dependency risk explicitly.
