# Current Analysis System State

## Snapshot Date
- `2026-04-19`

## Current Release Baseline
- analysis module baseline: `v1_0_1`
- status: offline analysis package is implemented through the original `A0-A7` split

Completed implementation wave:
- `A0` contracts and package split
- `A1` research universe and QA gate
- `A2` game-team and season mart profiles
- `A3` state panel and winner-definition profiles
- `A4` descriptive report pack
- `A5` reusable backtest engine
- `A6` interpretable predictive baselines
- `A7` player-impact shadow lane

## Current CLI Surface
- `build_analysis_mart`
- `build_analysis_report`
- `run_analysis_backtests`
- `train_analysis_baselines`

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
  - `15 passed`
  - `11 skipped`
- skipped checks are Postgres-gated integration validations behind `JANUS_RUN_DB_TESTS=1`
- CLI smoke passed:
  - `python -m app.data.pipelines.daily.nba.analysis_module -h`

## Current Gaps
- disposable Postgres and dev-clone database workflow is not yet formalized
- full non-live validation of mart, report, backtest, and model commands is still pending
- several-strategy comparison framework is not yet implemented
- random 5%-10% holdout benchmarking is not yet part of the standard workflow
- permanent frontend module does not exist yet

## Output Root Convention
- default analysis artifact root on this machine resolves to:
  - `C:\code-personal\janus-local\janus_cortex\archives\output\nba_analysis`
- this keeps artifact churn outside the repository root

## Current Truth Sources
- [app/docs/nba_analysis_module_plan.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/nba_analysis_module_plan.md)
- [app/docs/nba_analysis_data_products.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/nba_analysis_data_products.md)
- [app/docs/nba_analysis_modeling_and_backtesting.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/nba_analysis_modeling_and_backtesting.md)
- [app/docs/planning/current/roadmap_to_multi_algo_backtests.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/roadmap_to_multi_algo_backtests.md)
