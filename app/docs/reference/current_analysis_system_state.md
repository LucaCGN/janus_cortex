# Current Analysis System State

## Snapshot Date
- `2026-04-21`

## Current Release Baseline
- analysis module baseline: `v1_0_1`
- benchmark contract: `v8`
- status: validated through realistic execution replay, expanded family research, and a first master-router baseline

Completed implementation wave:
- `A0` contracts and package split
- `A1` research universe and QA gate
- `A2` game-team and season mart profiles
- `A3` state panel and winner-definition profiles
- `A4` descriptive report pack
- `A5` reusable backtest engine
- `A6` interpretable predictive baselines
- `A7` player-impact shadow lane

Completed release wave:
- `v1.0.2` safe DB validation workflow
- `v1.0.3` non-live validation workflow
- `v1.0.4` five-family strategy lab
- `v1.1.0` benchmarked multi-algorithm backtest workflow
- `v1.2.0` stable read-only consumer adapters
- `v1.3.0` permanent analysis studio alpha
- `v1.3.1` read-only family comparison follow-on lane
- `v1.4.0` sequential portfolio benchmark
- `v1.4.1` repeated-seed robustness and combined keep-family sleeve
- `v1.4.2` final strategy refinement, promoted underdog continuation, and first statistical routing lane
- `v1.4.3` realistic execution replay, promoted Q1/Q4 families, daily bankroll-path artifacts, and per-game strategy classification
- `v1.4.4` expanded family research, promoted favorite-panic and halftime-Q3 methods, and master-router baseline

## Current CLI Surface
- `build_analysis_mart`
- `build_analysis_report`
- `run_analysis_backtests`
- `train_analysis_baselines`

## Corpus Snapshot
- season: `2025-26`
- phase: `regular_season`
- finished games: `1224`
- research-ready games: `1198`
- descriptive-only games: `26`
- coverage-status counts:
  - `covered_pre_and_ingame=1198`
  - `covered_partial=13`
  - `no_history=10`
  - `no_matching_event=2`
  - `pregame_only=1`

## Mart Snapshot
- `nba.nba_analysis_game_team_profiles=2448`
- `nba.nba_analysis_state_panel=1379024`

## Current Building Block Set
The current deterministic stack is split into:
- `A` routed core families that compete per game on confidence
- `B` independent trigger sleeves that can fire as extra positions in their own quarters

### Routed Core Families
#### `winner_definition`
- rule: buy `80c`, break `75c` or `76c` for stronger scoreboard control
- full-sample 100-game ending bankroll: `2,490.05`
- 10-seed mean ending bankroll: `295,300.59`
- 10-seed median ending bankroll: `141,733.35`
- 10-seed worst drawdown: `59.56%`

#### `inversion`
- rule: dynamic `45c/50c` continuation with momentum and scoreboard guardrails, exit below `49c`
- full-sample 100-game ending bankroll: `47,534,677.74`
- 10-seed mean ending bankroll: `15,401.84`
- 10-seed median ending bankroll: `2,344.44`
- 10-seed worst drawdown: `45.88%`

#### `underdog_liftoff`
- rule: sub-`42c` opener, rebound through `36c`, momentum `>=1`, score diff `>=-4`, exit `50c` or `-3c`
- full-sample 100-game ending bankroll: `23,491,618.95`
- 10-seed mean ending bankroll: `60.33`
- 10-seed median ending bankroll: `42.01`
- 10-seed worst drawdown: `37.40%`

#### `favorite_panic_fade_v1`
- rule: buy strong pregame favorites after a panic selloff only once they recross into stability, then exit on reclaim toward the mid-`60c` range, `+8c`, or a renewed break
- full-sample 100-game ending bankroll: `14,811.37`
- 10-seed mean ending bankroll: `26.22`
- 10-seed median ending bankroll: `26.25`
- 10-seed worst drawdown: `9.66%`

### Independent Trigger Sleeves
#### `q1_repricing`
- rule: for `25c-75c` openers, buy the first-Q1 continuation once price gains `7c` and clears `52c`, with momentum `>=3` and score diff `>=1`
- full-sample 100-game ending bankroll: `2,820.72`
- 10-seed mean ending bankroll: `16.63`
- 10-seed median ending bankroll: `16.01`
- 10-seed worst drawdown: `19.07%`

#### `halftime_q3_repricing_v1`
- rule: buy the early-Q3 continuation after halftime once price gains `5c`, momentum is positive, and the path stays stable enough to target a `+7c` continuation before Q3 ends
- full-sample 100-game ending bankroll: `1,035.01`
- 10-seed mean ending bankroll: `14.10`
- 10-seed median ending bankroll: `13.97`
- 10-seed worst drawdown: `7.93%`

#### `q4_clutch`
- rule: in the last `300` seconds of Q4, buy the side that reclaims `55c` in a close game after repeated lead changes, then exit on the next extension or break-back
- full-sample 100-game ending bankroll: `112,582.58`
- 10-seed mean ending bankroll: `26.47`
- 10-seed median ending bankroll: `29.15`
- 10-seed worst drawdown: `10.71%`

### Experimental And Deferred Research
- `comeback_reversion_v2`
  - improved over the original comeback family, but still too path-dependent for promotion
  - full-sample 100-game ending bankroll: `0.81`
  - 10-seed mean ending bankroll: `12.21`
  - 10-seed positive-seed rate: `40%`
- `model_residual_dislocation_v1`
  - deferred until the strategy interface supports a clean fit/apply split without sample leakage

Rejected families:
- `reversion`
- `comeback_reversion`
- `volatility_scalp`

## Current Portfolio Surface
### Combined Keep-Family Sleeve
- family: `combined_keep_families`
- members: `favorite_panic_fade_v1,halftime_q3_repricing_v1,inversion,q1_repricing,q4_clutch,underdog_liftoff,winner_definition`
- full-sample ending bankroll: `96,805.76`
- full-sample max drawdown: `35.91%`
- full-sample executed trades: `72`
- interpretation: useful as a complementary sleeve reference, but still not the end-state controller for the product design

### Deterministic Routed Sleeve
- family: `statistical_routing_v1`
- full-sample ending bankroll: `522,883.17`
- full-sample max drawdown: `34.72%`
- full-sample executed trades: `80`
- opening-band map:
  - `10-20`: `underdog_liftoff`
  - `20-30`: `underdog_liftoff`
  - `30-40`: `inversion`
  - `40-50`: `inversion`
  - `50-60`: `winner_definition`
  - `60-70`: `q4_clutch`
  - `70-80`: `favorite_panic_fade_v1`
  - `80-90`: `favorite_panic_fade_v1`
  - `90-100`: `winner_definition`
- interpretation: still useful as a reference baseline, but it is now secondary to the confidence-based master router

### Master Router Baseline
- family: `master_strategy_router_v1`
- core families: `winner_definition,inversion,underdog_liftoff,favorite_panic_fade_v1`
- extra sleeves: `q1_repricing,halftime_q3_repricing_v1,comeback_reversion_v2,q4_clutch`
- selection sample: `time_train`
- full-sample ending bankroll: `5,396.87`
- time-validation ending bankroll: `1,915.95`
- random-holdout ending bankroll: `1,366,218.64`
- current interpretation:
  - it already beats `winner_definition` on `full_sample`, `time_validation`, and `random_holdout`
  - it is directionally valid as the first deterministic controller for the end-product design
  - its robustness across repeated seeds is not yet frozen as a canonical artifact

### New Artifact Surface
- `benchmark_portfolio_daily_paths`
  - day-by-day bankroll path for each strategy and sleeve across the 100-game replay
- `portfolio_charts/*.svg`
  - per-strategy bankroll-path charts saved under the backtest artifact bundle
- `benchmark_game_strategy_classification`
  - realized best-strategy-by-game reference table for later routing and context-model work
- `benchmark_master_router_decisions`
  - per-game core-family decision log with routed confidence components and triggered sleeve inventory

## Validation Snapshot
Validated on `2026-04-21`:
- `python -m pytest -q tests/app/data/pipelines/daily/nba/test_analysis_backtests_pytest.py`
  - `8 passed`
- `python -m app.data.pipelines.daily.nba.analysis_module run_analysis_backtests --season 2025-26 --season-phase regular_season --portfolio-initial-bankroll 10 --portfolio-position-size-fraction 1.0 --portfolio-min-order-dollars 1 --portfolio-min-shares 5 --portfolio-max-concurrent-positions 3 --portfolio-concurrency-mode shared_cash_equal_split --robustness-seeds 1107,2113,3251,4421,5573,6659,7873,9011,10243,11519`
  - completed successfully
- `python -m app.data.pipelines.daily.nba.analysis_module build_analysis_report --season 2025-26 --season-phase regular_season`
  - completed successfully

## Current Frontend Surface
- permanent studio routes remain read-only
- current focus is not new frontend math
- next frontend step is portfolio and robustness visualization against frozen artifact contracts

## Current Gaps
- master-router repeated-seed robustness is not yet a frozen artifact
- the confidence weighting still over-selects `winner_definition` relative to the strong standalone `favorite_panic_fade_v1` results
- the new concurrent-position engine is in place, but the current regular-season 100-game replay still rarely binds above two open positions
- `comeback_reversion_v2` remains experimental and should not be promoted into the routed core yet
- `model_residual_dislocation_v1` still needs a split-safe training interface
- context models for the promoted families still need to be built and compared against deterministic routing
- season-continuity branches for playoffs/preseason and WNBA are still pending

## Current Next Branches
- `codex/analysis-routing-allocation`
- `codex/analysis-context-models`
- `codex/frontend-analysis-portfolio-viz`
- sidecars:
  - `codex/season-playoffs-preseason`
  - `codex/season-wnba-bootstrap`

## Output Root Convention
- default analysis artifact root on this machine:
  - `C:\code-personal\janus-local\janus_cortex\archives\output\nba_analysis`

## Current Truth Sources
- [app/docs/reference/master_execution_dependency_graph.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/reference/master_execution_dependency_graph.md)
- [app/docs/reference/analysis_sampling_benchmarking_reference.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/reference/analysis_sampling_benchmarking_reference.md)
- [app/docs/reference/analysis_sequential_portfolio_benchmarking_reference.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/reference/analysis_sequential_portfolio_benchmarking_reference.md)
- [app/docs/planning/current/roadmap_to_multi_algo_backtests.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/roadmap_to_multi_algo_backtests.md)
- [app/docs/planning/current/branches/README.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/README.md)
