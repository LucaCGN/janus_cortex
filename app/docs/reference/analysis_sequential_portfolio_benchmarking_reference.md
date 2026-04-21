# Sequential Portfolio Benchmarking Reference

## Purpose
Define the canonical bankroll-simulation contract for the NBA analysis module and freeze the current validated outcomes for:
- single-family sequential replay
- repeated-seed robustness
- the combined keep-family sleeve
- the opening-band routed sleeve

## Canonical Contract
Sequential portfolio benchmarking extends `run_analysis_backtests` under benchmark contract `v7`.

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

## Current Validated Family Status
Validated on `2026-04-20` against season `2025-26`, phase `regular_season`, starting bankroll `$10.00`, position fraction `1.0`, `max_concurrent_positions=3`, and the first `100` chronological games.

### `inversion`
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

### `winner_definition`
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

### `underdog_liftoff`
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

### `q1_repricing`
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

### `q4_clutch`
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

### Rejected Families
- `reversion`
  - stable negative under the 10-seed lens
- `comeback_reversion`
  - mixed with only `1/10` positive seeds
- `volatility_scalp`
  - mixed with only `1/10` positive seeds

## Combined Keep-Family Sleeve
Members:
- `inversion`
- `q1_repricing`
- `q4_clutch`
- `underdog_liftoff`
- `winner_definition`

Current result:
- full-sample ending bankroll `47,057.42`
- full-sample max drawdown `28.15%`
- full-sample executed trades `63`

Interpretation:
- useful as a diversification surface
- the added Q1 and Q4 sleeves make the combined lane materially stronger, but it still trails the top standalone inversion run

## Statistical Routing
Current band map:
- `10-20`: `underdog_liftoff`
- `20-30`: `underdog_liftoff`
- `30-40`: `inversion`
- `40-50`: `inversion`
- `50-60`: `winner_definition`
- `60-70`: `q4_clutch`
- `70-80`: `winner_definition`
- `80-90`: `winner_definition`
- `90-100`: `winner_definition`

Current routed result:
- full-sample ending bankroll `93,769.92`
- full-sample max drawdown `20.38%`
- full-sample executed trades `54`

Interpretation:
- opening band is enough to support deterministic family routing
- the routed sleeve now hands the `60-70` band to the new clutch family
- routed performance is materially stronger than the prior freeze, but still below the strongest standalone inversion run

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
