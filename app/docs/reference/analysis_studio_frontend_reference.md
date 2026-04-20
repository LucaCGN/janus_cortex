# Analysis Studio Frontend Reference

## Purpose
Define the stable architecture for the permanent analysis frontend.

This document is reference, not planning. It describes what the frontend is allowed to depend on and which runtime choices are currently true.

## Current Architecture Decision
- the permanent frontend lives under `frontend/analysis_studio/*`
- the first implementation uses the existing FastAPI runtime and serves static assets directly
- `F1` does not introduce a Node or separate JS build toolchain because the repository does not currently own one
- the frontend consumes the analysis consumer snapshot contract instead of loading raw artifact JSONs independently
- local operator run control also stays in the existing FastAPI runtime for now, but it is limited to whitelisted commands and local workspace paths
- `F3a` adds a mart-backed game explorer through thin read-only API routes, so the frontend still does not parse analysis artifacts directly
- `F4a` adds a read-only family comparison surface, and the studio now consumes a separate backtest detail contract for that view

## Stable Read Surface
- page route:
  - `GET /analysis-studio`
- read-only snapshot route:
  - `GET /v1/analysis/studio/snapshot`
- control-plane route:
  - `GET /v1/analysis/studio/control`
- game explorer routes:
  - `GET /v1/analysis/studio/games`
  - `GET /v1/analysis/studio/games/{game_id}`
- run-registry routes:
  - `GET /v1/analysis/studio/runs`
  - `GET /v1/analysis/studio/runs/{run_id}`
- local run-launch route:
  - `POST /v1/analysis/studio/runs`
- backtest comparison routes:
  - `GET /v1/analysis/studio/backtests`
  - `GET /v1/analysis/studio/backtests/{strategy_family}`

Query parameters for the snapshot route align with `AnalysisConsumerRequest`:
- `season`
- `season_phase`
- `analysis_version`
- `backtest_experiment_id`
- `output_root`

## Dependency Rules
The frontend should read:
- `load_analysis_consumer_snapshot`
- `AnalysisConsumerRequest`
- static report and benchmark information already normalized by the adapter layer
- the studio game explorer routes for finished-game profile and state-panel inspection
- the studio backtest comparison routes for family index and bounded detail reads

The frontend should not read:
- raw ingest tables
- report, backtest, or model JSON files directly
- `nba_analysis_game_team_profiles` or `nba_analysis_state_panel` files directly from the browser
- mutable sync endpoints as its primary offline analysis source
- arbitrary shell commands or arbitrary filesystem paths through the studio run controls

## Current Operator Rules
- run launches are limited to:
  - `run_analysis_validation`
  - `build_analysis_mart`
  - `build_analysis_report`
  - `run_analysis_backtests`
  - `train_analysis_baselines`
- studio-launched runs write under `JANUS_LOCAL_ROOT\archives\output\nba_analysis_studio_runs\...`
- validation history is discovered from `JANUS_LOCAL_ROOT\archives\output\nba_analysis_validation\...`
- available analysis versions are resolved from the dynamic default analysis root, not hard-coded into the frontend

## Current Explorer Rules
- the game explorer is read-only and bounded
- game index rows come from existing `nba_analysis_game_team_profiles` outputs
- game detail state windows come from existing `nba_analysis_state_panel` outputs
- the backend may read CSV or parquet artifacts, but the browser only depends on the normalized route payloads

## Current Module Layout
- `frontend/analysis_studio/index.html`
- `frontend/analysis_studio/static/analysis_studio.css`
- `frontend/analysis_studio/static/analysis_studio.js`
- `app/api/routers/analysis_studio.py`

## Completed And Deferred Frontend Subphases
- completed on the current branch:
  - `F1` scaffolded the permanent frontend module
  - `F2` added run control, validation visibility, and local operator state
  - `F3a` added mart-backed game explorer routes plus frontend game index/detail panels
  - `F4a` added read-only family comparison routes and studio views
- deferred:
  - `F3b` richer overlays only if the current explorer proves insufficient
  - `F4b` richer comparison UX refinements, if needed, after the read-only comparison contract is stable
  - `F5` operator hardening after the comparison surface settles

## Constraint
If a future frontend branch needs a separate JS toolchain, that decision should be explicit and documented as a branch-level architecture change rather than introduced opportunistically.
