# Branch Plan: `codex/frontend-analysis-studio`

## Role
Permanent frontend branch for the analysis program.

## Target Milestone
- `v1.3.0`

## Depends On
- `codex/analysis-a8-consumer-adapters`

## Owns
- dedicated frontend module
- operator workflows for triggering and reviewing analysis runs
- visual inspection of strategies, models, and game context
- thin read-only API integration required to serve the permanent frontend

Likely write scope:
- `frontend/analysis_studio/*`
- `app/api/routers/analysis_studio.py`
- frontend-specific docs
- read-only consumer integration

## Does Not Own
- raw ingest or DB migration logic
- backtest engine math
- sandbox-only prototypes as the long-term solution

## Subphases

### `F1` Frontend Module Scaffold
Objective:
- create the permanent frontend module outside `app/sandboxes`

Current execution decision:
- `F1` uses the existing FastAPI runtime and static assets under `frontend/analysis_studio/*`
- do not introduce a repo-owned Node toolchain in this subphase unless the branch explicitly changes architecture
- the first UI surface consumes `load_analysis_consumer_snapshot` through a thin read-only API route

### `F2` Run Control Surface
Objective:
- let operators trigger and monitor mart, report, backtest, and model runs

Immediate slice order:
- `F2a` show the latest validation and artifact snapshots already written under `JANUS_LOCAL_ROOT`
- `F2b` add guarded local-only run actions for mart, report, backtests, and baselines
- `F2c` surface run status, stdout/stderr log paths, and last-output links inside the studio

Current branch state:
- `F2a` is implemented through the studio control route and validation-history panels
- `F2b` is implemented through a whitelisted local run launcher for validation, mart, report, backtests, and baselines
- `F2c` is implemented through the in-memory run registry plus stdout/stderr and output-root tracking
- `F3a` is implemented as a thin read-only facade over `nba_analysis_game_team_profiles` and `nba_analysis_state_panel`
- the studio now exposes a finished-game explorer index plus bounded per-game detail views without introducing new storage or frontend-owned artifact parsing
- this branch now stops at the validated `F3a` studio alpha surface
- richer `F3b` overlays are deferred unless a later branch proves they are needed
- deeper `F4` strategy comparison depends on a downstream read-only comparison contract and should not be mixed into this PR

### `F3` Game Context Explorer
Objective:
- display game-level and state-level context with analysis overlays

Immediate slice order:
- `F3a` implemented:
  - `GET /v1/analysis/studio/games`
  - `GET /v1/analysis/studio/games/{game_id}`
  - frontend game index and detail panels backed by existing analysis artifacts
- `F3b` deferred:
  - only reopen if the later comparison or operator branches prove the mart-backed explorer is insufficient

### `F4` Strategy Comparison Views
Objective:
- surface per-strategy leaderboards, trade traces, MFE/MAE, and hold-time distributions

Execution rule:
- do not implement `F4` directly on this branch
- first land a separate analysis-owned read-only comparison contract branch for per-family benchmark detail
- then stack a follow-on frontend branch on top of this branch to consume that contract

### `F5` Operator UX Hardening
Objective:
- refine the workflows for repeated research usage, not one-off demos

## Merge Gate
- permanent frontend module exists
- frontend reads only stable read-only contracts
- operators can inspect normalized snapshots, validation state, guarded local runs, and finished-game explorer views without direct artifact parsing

## Consumer Contract To Use
Frontend should treat the A8 adapter layer as the only supported backend surface for offline analysis reads:
- `load_analysis_consumer_snapshot`
- `AnalysisConsumerRequest`

Frontend should not:
- guess version directories manually
- parse nested benchmark/report/model JSONs independently
- read raw ingest tables directly

## First Implementation Slice
- serve `/analysis-studio` from the existing FastAPI app
- serve static assets from `frontend/analysis_studio/static`
- expose a single read-only snapshot route aligned to `AnalysisConsumerRequest`
- keep later subphases free to decide whether run control should remain in FastAPI or move behind another boundary

## Current Route Surface
- `GET /analysis-studio`
- `GET /v1/analysis/studio/snapshot`
- `GET /v1/analysis/studio/control`
- `GET /v1/analysis/studio/games`
- `GET /v1/analysis/studio/games/{game_id}`
- `GET /v1/analysis/studio/runs`
- `GET /v1/analysis/studio/runs/{run_id}`
- `POST /v1/analysis/studio/runs`

## Parallelization Note
- keep frontend work inside `frontend/analysis_studio/*`, `app/api/routers/analysis_studio.py`, and frontend reference docs
- do not overlap frontend branch edits with season branches touching NBA ingest, schema, or playoff/WNBA planning files

## Handoff
Next dependent branches:
- `codex/analysis-backtest-detail-contract`
- a stacked frontend comparison branch after that contract exists
