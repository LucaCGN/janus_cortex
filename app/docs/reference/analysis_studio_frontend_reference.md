# Analysis Studio Frontend Reference

## Purpose
Define the stable architecture for the permanent analysis frontend while the branch is still in its early scaffold phase.

This document is reference, not planning. It describes what the frontend is allowed to depend on and which runtime choices are currently true.

## Current Architecture Decision
- the permanent frontend lives under `frontend/analysis_studio/*`
- the first implementation uses the existing FastAPI runtime and serves static assets directly
- `F1` does not introduce a Node or separate JS build toolchain because the repository does not currently own one
- the frontend consumes the analysis consumer snapshot contract instead of loading raw artifact JSONs independently
- local operator run control also stays in the existing FastAPI runtime for now, but it is limited to whitelisted commands and local workspace paths

## Stable Read Surface
- page route:
  - `GET /analysis-studio`
- read-only snapshot route:
  - `GET /v1/analysis/studio/snapshot`
- control-plane route:
  - `GET /v1/analysis/studio/control`
- run-registry routes:
  - `GET /v1/analysis/studio/runs`
  - `GET /v1/analysis/studio/runs/{run_id}`
- local run-launch route:
  - `POST /v1/analysis/studio/runs`

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

The frontend should not read:
- raw ingest tables
- report, backtest, or model JSON files directly
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

## Current Module Layout
- `frontend/analysis_studio/index.html`
- `frontend/analysis_studio/static/analysis_studio.css`
- `frontend/analysis_studio/static/analysis_studio.js`
- `app/api/routers/analysis_studio.py`

## Intent For Later Frontend Subphases
- `F2` adds run control and operator workflow state
- `F3` adds game and state context exploration
- `F4` adds deeper strategy comparison and trade-trace views
- `F5` hardens operator UX for repeated research work

## Constraint
If a future frontend branch needs a separate JS toolchain, that decision should be explicit and documented as a branch-level architecture change rather than introduced opportunistically.
