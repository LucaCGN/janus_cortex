# Sequential Portfolio Benchmarking Reference

## Purpose
Define the canonical bankroll-simulation contract for the NBA analysis module and freeze the current validated outcomes for:
- single-family sequential replay
- repeated-seed robustness
- the combined keep-family sleeve
- the opening-band routed sleeve
- the confidence-based master router baseline
- the restrained LLM-router finalist benchmark and shared showdown replay

## Canonical Contract
Sequential portfolio benchmarking extends `run_analysis_backtests` under benchmark contract `v10`.

Active defaults:
- starting bankroll: `10.0`
- position size fraction: `1.0`
- game limit: `100`
- exchange floor:
  - minimum order dollars: `1.0`
  - minimum shares: `5.0`
- concurrency config:
  - max concurrent positions: `3`
  - mode: `shared_cash_equal_split`
- split set:
  - `full_sample`
  - `time_train`
  - `time_validation`
  - `random_train`
  - `random_holdout`
- frozen robustness seeds:
  - `1107`
  - `2113`
  - `3251`
  - `4421`
  - `5573`
  - `6659`
  - `7873`
  - `9011`
  - `10243`
  - `11519`

Determinism rules:
- trade order is sorted by `entry_at` with stable tie-breakers
- the replay uses the first `100` chronological games in the selected sample
- final-state entries are discarded
- the same input and seed produce the same bankroll path
- the minimum executable stake is `max($1.00, 5 * entry_price)`
- open positions reserve cash until their exit event settles

Artifact additions in `v7`:
- `benchmark_portfolio_daily_paths`
- `benchmark_game_strategy_classification`
- `portfolio_charts/*.svg`

Artifact additions in `v8`:
- `benchmark_master_router_decisions`

Artifact additions in `v9`:
- `benchmark_llm_experiment_lane_summary`
- `benchmark_llm_experiment_decisions`
- `benchmark_llm_experiment_summary`

Artifact additions in `v10`:
- `benchmark_llm_experiment_showdown_summary`
- `benchmark_llm_experiment_showdown_daily_paths`
- finalist-focused consumer snapshot payload for the analysis studio dashboard

## Current Building-Block Status
Validated on `2026-04-21` against season `2025-26`, phase `regular_season`, starting bankroll `$10.00`, position fraction `1.0`, `max_concurrent_positions=3`, and the first `100` chronological games.

### Routed Core Families
#### `winner_definition`
- rule:
  - buy once the market reaches `80c`
  - widen the break line from `75c` to `76c` only when entry score margin is already `8+`
- full-sample result:
  - ending bankroll `2,490.05`
  - max drawdown `24.49%`
  - executed trades `50`
- 10-seed robustness:
  - positive seeds `10/10`
  - mean ending bankroll `295,300.59`
  - median ending bankroll `141,733.35`
  - ending bankroll range `45,249.66` to `1,626,989.21`
  - worst drawdown `59.56%`

#### `inversion`
- rule:
  - buy the underdog once it crosses `45c` for openers below `25c`, otherwise on the `50c` cross
  - require recent momentum and avoid badly trailing scoreboard states
  - exit if the path breaks back below `49c` or the game ends
- full-sample result:
  - ending bankroll `47,534,677.74`
  - max drawdown `29.11%`
  - executed trades `79`
- 10-seed robustness:
  - positive seeds `10/10`
  - mean ending bankroll `15,401.84`
  - median ending bankroll `2,344.44`
  - ending bankroll range `248.95` to `124,015.18`
  - worst drawdown `45.88%`

#### `underdog_liftoff`
- rule:
  - buy openers below `42c` once they rebound through `36c`
  - require momentum `>= 1`, at least `900` seconds left, and no worse than a `4` point deficit
  - exit at `50c`, on a `3c` stop, or at the end
- full-sample result:
  - ending bankroll `23,491,618.95`
  - max drawdown `50.12%`
  - executed trades `95`
- 10-seed robustness:
  - positive seeds `10/10`
  - mean ending bankroll `60.33`
  - median ending bankroll `42.01`
  - ending bankroll range `23.72` to `217.21`
  - worst drawdown `37.40%`

#### `favorite_panic_fade_v1`
- rule:
  - only for strong pregame favorites that suffer a panic selloff and then recross into stability
  - enter on the recross, then exit on reclaim toward the low-`60c` range, `+8c`, or renewed weakness
- full-sample result:
  - ending bankroll `14,811.37`
  - max drawdown `9.66%`
  - executed trades `42`
- 10-seed robustness:
  - positive seeds `10/10`
  - mean ending bankroll `26.22`
  - median ending bankroll `26.25`
  - ending bankroll range `14.10` to `40.13`
  - worst drawdown `9.66%`

### Independent Trigger Sleeves
#### `q1_repricing`
- rule:
  - eligible openers are now `25c-75c`
  - buy the first Q1 continuation once the price gains `7c` and clears `52c`
  - require first-quarter momentum `>= 3` and score diff `>= 1`
  - exit at `+8c`, on a `5c` break, or at the end of Q1
- full-sample result:
  - ending bankroll `2,820.72`
  - max drawdown `20.43%`
  - executed trades `90`
- 10-seed robustness:
  - positive seeds `10/10`
  - mean ending bankroll `16.63`
  - median ending bankroll `16.01`
  - ending bankroll range `11.53` to `21.74`
  - worst drawdown `19.07%`

#### `halftime_q3_repricing_v1`
- rule:
  - buy the early-Q3 continuation after halftime once price gains `5c`
  - require positive momentum and enough stability to target `+7c`, `-4c`, or end-of-Q3 flattening
- full-sample result:
  - ending bankroll `1,035.01`
  - max drawdown `14.70%`
  - executed trades `49`
- 10-seed robustness:
  - positive seeds `9/10`
  - mean ending bankroll `14.10`
  - median ending bankroll `13.97`
  - ending bankroll range `9.37` to `21.27`
  - worst drawdown `7.93%`

#### `q4_clutch`
- rule:
  - buy in the last `300` seconds of Q4 when a close game with repeated lead changes reclaims `55c`
  - require positive recent momentum and no more than a `6` point margin
  - exit on the next extension, a break-back, or game end
- full-sample result:
  - ending bankroll `112,582.58`
  - max drawdown `13.42%`
  - executed trades `41`
- 10-seed robustness:
  - positive seeds `9/10`
  - mean ending bankroll `26.47`
  - median ending bankroll `29.15`
  - ending bankroll range `10.00` to `38.36`
  - worst drawdown `10.71%`

### Experimental Family
#### `comeback_reversion_v2`
- rule:
  - buy trailing underdogs in the Q3 reset window only after the rebound has started and the snapback still has room
  - exit at `+8c`, `-5c`, or game end
- full-sample result:
  - ending bankroll `0.81`
  - max drawdown `94.68%`
  - executed trades `39`
- 10-seed robustness:
  - positive seeds `4/10`
  - mean ending bankroll `12.21`
  - median ending bankroll `8.19`
  - ending bankroll range `2.58` to `31.69`
  - worst drawdown `82.58%`
- interpretation:
  - improved enough to keep researching, but not robust enough to promote as a building block

### Rejected Or Deferred Families
- `reversion`
  - stable negative under the 10-seed lens
- `comeback_reversion`
  - mixed with only `1/10` positive seeds
- `volatility_scalp`
  - mixed with only `1/10` positive seeds
- `model_residual_dislocation_v1`
  - deferred until the backtest interface can separate training and inference cleanly for model-based residual signals

## Combined Keep-Family Sleeve
Members:
- `favorite_panic_fade_v1`
- `halftime_q3_repricing_v1`
- `inversion`
- `q1_repricing`
- `q4_clutch`
- `underdog_liftoff`
- `winner_definition`

Current result:
- full-sample ending bankroll `96,805.76`
- full-sample max drawdown `35.91%`
- full-sample executed trades `72`

Interpretation:
- useful as a diversification surface and sanity check
- the expanded keep set is stronger than earlier freezes, but the combined sleeve is still not the product controller

## Statistical Routing
Current band map:
- `10-20`: `underdog_liftoff`
- `20-30`: `underdog_liftoff`
- `30-40`: `inversion`
- `40-50`: `inversion`
- `50-60`: `winner_definition`
- `60-70`: `q4_clutch`
- `70-80`: `favorite_panic_fade_v1`
- `80-90`: `favorite_panic_fade_v1`
- `90-100`: `winner_definition`

Current routed result:
- full-sample ending bankroll `522,883.17`
- full-sample max drawdown `34.72%`
- full-sample executed trades `80`

Interpretation:
- opening band is enough to support deterministic family routing
- the opening-band map now captures favorite-panic states in the `70-90` band
- this is now a strong baseline reference, but not the final design for the controller

## Master Router Baseline
Current master router design:
- core families:
  - `winner_definition`
  - `inversion`
  - `underdog_liftoff`
  - `favorite_panic_fade_v1`
- extra sleeves:
  - `q1_repricing`
  - `halftime_q3_repricing_v1`
  - `comeback_reversion_v2`
  - `q4_clutch`
- training source for priors:
  - `time_train`
- per-game confidence uses:
  - `opening_band`
  - `period_label`
  - `context_bucket`
  - `signal_strength`

Current master router result:
- full-sample ending bankroll `5,396.87`
- time-validation ending bankroll `1,915.95`
- random-holdout ending bankroll `1,366,218.64`

Interpretation:
- the first confidence-based controller is valid and beats `winner_definition` on the main held-out controller slices
- it remains the strongest deterministic bankroll engine even after the expanded LLM pass

## Restrained LLM Router Benchmark
Validated on `2026-04-21` under:
- model: `gpt-5.4-mini`
- iteration count: `10`
- games per iteration: `30`
- showdown replay: shared fixed `100`-game sample across the top finalists

Current restrained LLM variant leaders:
- `llm_hybrid_compact_guarded_v1`
  - mean ending bankroll `379.51`
  - mean drawdown `25.90%`
  - interpretation: best current LLM balance of return, stability, and bounded autonomy
- `llm_hybrid_compact_v1`
  - mean ending bankroll `330.12`
  - mean drawdown `21.91%`
- `llm_hybrid_restrained_v1`
  - mean ending bankroll `268.89`
  - mean drawdown `17.68%`
- `winner_definition`
  - mean ending bankroll `274.73`
  - mean drawdown `24.02%`
- `master_strategy_router_v1`
  - mean ending bankroll `558.15`
  - mean drawdown `35.21%`

Shared finalist showdown on the same `100` games:
- `master_strategy_router_v1`
  - ending bankroll `1,584,212.75`
  - max drawdown `53.97%`
- `llm_hybrid_compact_guarded_v1`
  - ending bankroll `628,041.16`
  - max drawdown `21.69%`
- `llm_hybrid_compact_v1`
  - ending bankroll `568,726.74`
  - max drawdown `21.69%`
- `llm_hybrid_compact_no_rationale_v1`
  - ending bankroll `423,599.61`
  - max drawdown `53.97%`
- `winner_definition`
  - ending bankroll `388,365.85`
  - max drawdown `23.28%`
- `llm_hybrid_restrained_v1`
  - ending bankroll `378,704.45`
  - max drawdown `18.74%`

Current interpretation:
- the best LLM variants are now materially competitive, not just drawdown filters
- the deterministic master router still wins on pure bankroll growth
- the compact guarded LLM lane is the best next override candidate because it improves the drawdown profile without collapsing returns
- medium-reasoning compact routing did not justify its extra complexity
  - context win rate
  - context average return
  - context support

Current result:
- full-sample ending bankroll `5,396.87`
- time-validation ending bankroll `1,915.95`
- random-holdout ending bankroll `1,366,218.64`

Interpretation:
- it already beats `winner_definition` on `full_sample`, `time_validation`, and `random_holdout`
- it is the first deterministic approximation of the intended end-product controller
- repeated-seed robustness for the router itself is not yet frozen in the canonical contract

## Best-Strategy-By-Game Reference
`benchmark_game_strategy_classification` is now part of the canonical artifact pack.

Current full-sample realized best-family counts:
- `winner_definition`: `752`
- `comeback_reversion`: `137`
- `inversion`: `120`
- `reversion`: `89`
- `underdog_liftoff`: `52`
- `q4_clutch`: `23`
- `volatility_scalp`: `17`
- `q1_repricing`: `8`
- `comeback_reversion_v2`: `16`
- `favorite_panic_fade_v1`: `11`
- `halftime_q3_repricing_v1`: `4`

Interpretation:
- this artifact is a routing and modeling reference, not a promotion rule
- the realized best family in a game is not the same thing as a robust standalone sleeve

## Rejected Refinements
Rejected or superseded variants from the refinement pass:
- `inversion` low-open cut expansion above `30c`
- `winner_definition` stronger break-line variants
- weaker `underdog_liftoff` trigger zones around `38c` and wider `4c` stops
- the narrower `q1_repricing` prototype that used `30c-70c`, `+8c`, `52c+`, momentum `>=4`, and score diff `>=2`

## Promotion Rule
A family is not promoted on win rate alone.

Promotion requires:
- positive full-sample sequential result
- positive time-validation and random-holdout benchmark signal
- acceptable drawdown
- repeated-seed robustness that remains directionally defensible
- promotion can be either:
  - a routed core family
  - an independently triggered sleeve
