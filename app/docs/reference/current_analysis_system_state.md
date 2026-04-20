# Current Analysis System State

## Snapshot Date
- `2026-04-20`

## Current Release Baseline
- analysis module baseline: `v1_0_1`
- status: validated branch state now extends through post-baseline safety, validation, strategy, benchmarking, and read-only consumer-adapter lanes

Completed implementation wave:
- `A0` contracts and package split
- `A1` research universe and QA gate
- `A2` game-team and season mart profiles
- `A3` state panel and winner-definition profiles
- `A4` descriptive report pack
- `A5` reusable backtest engine
- `A6` interpretable predictive baselines
- `A7` player-impact shadow lane
- `v1.0.2` disposable/dev-clone DB safety workflow
- `v1.0.3` non-live validation workflow
- `v1.0.4` five-family strategy lab
- `v1.1.0` benchmarked multi-algorithm backtest workflow
- `v1.2.0` stable read-only consumer adapters
- `v1.3.0` permanent analysis studio alpha
- `v1.3.1` read-only family comparison follow-on lane

## Current CLI Surface
- `build_analysis_mart`
- `build_analysis_report`
- `run_analysis_backtests`
- `train_analysis_baselines`

## Current Read-Only Consumer Surface
- `AnalysisConsumerRequest`
- `list_available_analysis_versions`
- `resolve_analysis_consumer_paths`
- `load_analysis_consumer_bundle`
- `build_analysis_consumer_snapshot`
- `load_analysis_consumer_snapshot`
- `load_analysis_backtest_index`
- `load_analysis_backtest_family_detail`

Consumer contract notes:
- downstream consumers load versioned report, backtest, and model artifacts through one adapter layer
- the consumer snapshot includes normalized report sections, benchmark leaderboards, candidate-freeze labels, model track summaries, and artifact links
- the backtest detail contract exposes per-family index and bounded detail reads for comparison views
- validation now captures a consumer snapshot in the disposable non-live sweep

## Corpus Snapshot
- season: `2025-26`
- phase: `regular_season`
- finished games: `1224`
- linked Polymarket events: `1222`
- feature snapshots: `1224`
- `covered_pre_and_ingame`: `1198`
- `research_ready`: `1198`
- `descriptive_only`: `26`
- residual classes:
  - `no_history=10`
  - `covered_partial=13`
  - `pregame_only=1`
  - `no_matching_event=2`

Historical note:
- older drafts referenced `1209` research-ready games
- current restored corpus resolves to `1198` because `11` additional games now fall into `covered_partial`

## Current Mart Snapshot
- `nba.nba_analysis_game_team_profiles=2448`
- `nba.nba_analysis_state_panel=1379024`

## Validation Snapshot
- analysis pytest sweep:
  - branch-local critical-path sweep includes consumer adapter tests
  - frontend studio router and static asset coverage now includes the F3 game explorer routes plus F4a backtest comparison routes
- skipped checks are Postgres-gated integration validations behind `JANUS_RUN_DB_TESTS=1`
- CLI smoke passed:
  - `python -m app.data.pipelines.daily.nba.analysis_module -h`
- disposable non-live validation runner passed with consumer snapshot capture:
  - `C:\code-personal\janus-local\janus_cortex\archives\output\nba_analysis_validation\20260420_111338`

## Current Frontend Surface
- permanent frontend branch uses the existing FastAPI runtime and static assets
- current routes:
  - `GET /analysis-studio`
  - `GET /v1/analysis/studio/snapshot`
  - `GET /v1/analysis/studio/backtests`
  - `GET /v1/analysis/studio/backtests/{strategy_family}`
  - `GET /v1/analysis/studio/control`
  - `GET /v1/analysis/studio/games`
  - `GET /v1/analysis/studio/games/{game_id}`
  - `GET /v1/analysis/studio/runs`
  - `GET /v1/analysis/studio/runs/{run_id}`
  - `POST /v1/analysis/studio/runs`
- current frontend scope is read-only for analysis consumption plus guarded local run control
- studio now surfaces:
  - latest validation summary under `JANUS_LOCAL_ROOT`
  - available analysis versions under the default output root
  - in-memory local run registry with stdout/stderr and output-root tracking
  - finished-game explorer rows backed by `nba_analysis_game_team_profiles`
  - bounded home/away state-panel detail backed by `nba_analysis_state_panel`
  - read-only family comparison index and bounded per-family detail backed by the analysis consumer adapter layer

## Current Gaps
- richer comparison UX such as charts, cross-family overlays, and multi-family side-by-side views is still pending
- richer game-context overlays beyond the mart-backed explorer are optional follow-up work, not the immediate frontend dependency
- season-continuity branches for playoffs/preseason and WNBA are still pending
- operator hardening beyond the current run-control surface is still pending

## Output Root Convention
- default analysis artifact root on this machine resolves to:
  - `C:\code-personal\janus-local\janus_cortex\archives\output\nba_analysis`
- this keeps artifact churn outside the repository root

## Current Truth Sources
- [app/docs/nba_analysis_module_plan.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/nba_analysis_module_plan.md)
- [app/docs/nba_analysis_data_products.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/nba_analysis_data_products.md)
- [app/docs/nba_analysis_modeling_and_backtesting.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/nba_analysis_modeling_and_backtesting.md)
- [app/docs/planning/current/roadmap_to_multi_algo_backtests.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/roadmap_to_multi_algo_backtests.md)
