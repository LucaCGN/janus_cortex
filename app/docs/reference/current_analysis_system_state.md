# Current Analysis System State

## Snapshot Date
- `2026-04-20`

## Current Release Baseline
- analysis module baseline: `v1_0_1`
- benchmark contract: `v7`
- status: validated through realistic execution replay, quarter-specific family promotion, and richer portfolio artifacts

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

## Current Promoted Strategy Set
### `inversion`
- rule: dynamic `45c/50c` continuation with momentum and scoreboard guardrails, exit below `49c`
- full-sample 100-game ending bankroll: `47,534,677.74`
- 10-seed mean ending bankroll: `15,401.84`
- 10-seed median ending bankroll: `2,344.44`
- 10-seed worst drawdown: `45.88%`

### `winner_definition`
- rule: buy `80c`, break `75c` or `76c` for stronger scoreboard control
- full-sample 100-game ending bankroll: `2,490.05`
- 10-seed mean ending bankroll: `295,300.59`
- 10-seed median ending bankroll: `141,733.35`
- 10-seed worst drawdown: `59.56%`

### `underdog_liftoff`
- rule: sub-`42c` opener, rebound through `36c`, momentum `>=1`, score diff `>=-4`, exit `50c` or `-3c`
- full-sample 100-game ending bankroll: `23,491,618.95`
- 10-seed mean ending bankroll: `60.33`
- 10-seed median ending bankroll: `42.01`
- 10-seed worst drawdown: `37.40%`

### `q1_repricing`
- rule: for `25c-75c` openers, buy the first-Q1 continuation once price gains `7c` and clears `52c`, with momentum `>=3` and score diff `>=1`
- full-sample 100-game ending bankroll: `2,820.72`
- 10-seed mean ending bankroll: `16.63`
- 10-seed median ending bankroll: `16.01`
- 10-seed worst drawdown: `19.07%`

### `q4_clutch`
- rule: in the last `300` seconds of Q4, buy the side that reclaims `55c` in a close game after repeated lead changes, then exit on the next extension or break-back
- full-sample 100-game ending bankroll: `112,582.58`
- 10-seed mean ending bankroll: `26.47`
- 10-seed median ending bankroll: `29.15`
- 10-seed worst drawdown: `10.71%`

Rejected families:
- `reversion`
- `comeback_reversion`
- `volatility_scalp`

## Current Portfolio Surface
### Combined Keep-Family Sleeve
- family: `combined_keep_families`
- members: `inversion,q1_repricing,q4_clutch,underdog_liftoff,winner_definition`
- full-sample ending bankroll: `47,057.42`
- full-sample max drawdown: `28.15%`
- interpretation: stronger complementary sleeve now that Q1 and clutch continuations are in the keep set, but still not better than the two strongest standalone families

### Deterministic Routed Sleeve
- family: `statistical_routing_v1`
- full-sample ending bankroll: `93,769.92`
- full-sample max drawdown: `20.38%`
- opening-band map:
  - `10-20`: `underdog_liftoff`
  - `20-30`: `underdog_liftoff`
  - `30-40`: `inversion`
  - `40-50`: `inversion`
  - `50-60`: `winner_definition`
  - `60-70`: `q4_clutch`
  - `70-100`: `winner_definition`

### New Artifact Surface
- `benchmark_portfolio_daily_paths`
  - day-by-day bankroll path for each strategy and sleeve across the 100-game replay
- `portfolio_charts/*.svg`
  - per-strategy bankroll-path charts saved under the backtest artifact bundle
- `benchmark_game_strategy_classification`
  - realized best-strategy-by-game reference table for later routing and context-model work

## Validation Snapshot
Validated on `2026-04-20`:
- `python -m pytest -q tests/app/data/pipelines/daily/nba/test_analysis_backtests_pytest.py`
  - `7 passed`
- `python -m app.data.pipelines.daily.nba.analysis_module run_analysis_backtests --season 2025-26 --season-phase regular_season --portfolio-initial-bankroll 10 --portfolio-position-size-fraction 1.0 --portfolio-min-order-dollars 1 --portfolio-min-shares 5 --portfolio-max-concurrent-positions 3 --portfolio-concurrency-mode shared_cash_equal_split --robustness-seeds 1107,2113,3251,4421,5573,6659,7873,9011,10243,11519`
  - completed successfully
- `python -m app.data.pipelines.daily.nba.analysis_module build_analysis_report --season 2025-26 --season-phase regular_season`
  - completed successfully

## Current Frontend Surface
- permanent studio routes remain read-only
- current focus is not new frontend math
- next frontend step is portfolio and robustness visualization against frozen artifact contracts

## Current Gaps
- routed-sleeve robustness is not yet a frozen artifact
- the new concurrent-position engine is in place, but the current regular-season 100-game replay rarely binds above one open position under the promoted hold horizons
- best-strategy-by-game classification is a descriptive artifact only; it is not yet the live routing policy
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
