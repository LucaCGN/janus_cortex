# Analysis Studio Frontend Reference

## Purpose
Define the stable architecture for the permanent analysis frontend while the branch is still in its early scaffold phase.

This document is reference, not planning. It describes what the frontend is allowed to depend on and which runtime choices are currently true.

## Current Architecture Decision
- the permanent frontend lives under `frontend/analysis_studio/*`
- the first implementation uses the existing FastAPI runtime and serves static assets directly
- `F1` does not introduce a Node or separate JS build toolchain because the repository does not currently own one
- the frontend consumes the analysis consumer snapshot contract instead of loading raw artifact JSONs independently

## Stable Read Surface
- page route:
  - `GET /analysis-studio`
- read-only snapshot route:
  - `GET /v1/analysis/studio/snapshot`

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
