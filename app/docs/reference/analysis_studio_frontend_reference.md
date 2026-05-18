# Analysis Studio Frontend Reference

Status: superseded 2026-05-17

> The tracked `frontend/` module has been removed. Keep this document as historical context for the old analysis studio frontend. Current operation is backend-first through API endpoints, `codex_tool`, repo docs, GitHub issues, runtime handoffs, and Obsidian.

## Purpose
Define the historical architecture for the removed analysis frontend.

This document is historical reference, not current planning. It describes what the removed frontend depended on.

## Current Architecture Decision
- the former frontend lived under `frontend/analysis_studio/*`
- static assets are no longer served from the repo
- `F1` does not introduce a Node or separate JS build toolchain because the repository does not currently own one
- the former frontend consumed the analysis consumer snapshot contract instead of loading raw artifact JSONs independently
- the default studio page now also consumes a dedicated unified benchmark dashboard payload for shared lane comparison
- the benchmark dashboard now treats replay as the realism baseline and separates standard backtest, replay result, and live observed views explicitly
- local operator run control also stays in the existing FastAPI runtime for now, but it is limited to whitelisted commands and local workspace paths
- `F3a` added a mart-backed game explorer through thin read-only API routes, so the frontend did not parse analysis artifacts directly
- `F4a` adds a read-only family comparison surface, and the studio now consumes a separate backtest detail contract for that view
- `F4b` repurposed the main `/analysis-studio` page into the benchmark-control dashboard while keeping the bounded detail APIs available

## Remaining Backend Read Surface
- unified dashboard route:
  - `GET /v1/analysis/studio/benchmark-dashboard`
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

## Historical Dependency Rules
The former frontend read:
- `build_unified_benchmark_dashboard`
- `load_analysis_consumer_snapshot`
- `AnalysisConsumerRequest`
- normalized shared benchmark submission manifests or synthesized shared replay artifacts through the API layer
- normalized result-mode views instead of inferring replay or live semantics from flat counts alone
- static report and benchmark information already normalized by the adapter layer
- the studio game explorer routes for finished-game profile and state-panel inspection
- the studio backtest comparison routes for family index and bounded detail reads

The former frontend was not allowed to read:
- shared replay, ML, or LLM artifacts directly from the browser
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
- available analysis versions were resolved from the dynamic default analysis root, not hard-coded into the frontend

## Current Explorer Rules
- the game explorer is read-only and bounded
- game index rows come from existing `nba_analysis_game_team_profiles` outputs
- game detail state windows come from existing `nba_analysis_state_panel` outputs
- the backend may read CSV or parquet artifacts, but the browser only depends on the normalized route payloads

## Current Module Layout
- `app/api/routers/analysis_studio.py`

## Completed And Deferred Frontend Subphases
- historical completed subphases:
  - `F1` scaffolded the former frontend module
  - `F2` added run control, validation visibility, and local operator state
  - `F3a` added mart-backed game explorer routes plus frontend game index/detail panels
  - `F4a` added read-only family comparison routes and studio views
- `F4b` added the unified benchmark-control dashboard over the shared replay contract and future lane submissions
- `F4b` now also exposes lane ranking, compare-ready ranking, replay compare-ready/shadow/bench splits, explicit realism-gap metrics, and strict submission example paths for future ML and LLM manifests
- deferred:
  - `F3b` richer overlays only if the current explorer proves insufficient
  - `F4c` richer comparison UX refinements, if needed, after ML and LLM lanes publish their first shared submissions
  - `F5` operator hardening after the comparison surface settles

## Constraint
If a future frontend branch is reintroduced, that decision must be explicit, issue-backed, and documented as a source-of-truth architecture change rather than introduced opportunistically.
