# Sequential Portfolio Benchmarking Reference

## Purpose
Define the canonical bankroll-simulation contract for the NBA analysis module and freeze the current validated outcomes for:
- single-family sequential replay
- repeated-seed robustness
- the combined keep-family sleeve
- the opening-band routed sleeve

## Canonical Contract
Sequential portfolio benchmarking extends `run_analysis_backtests` under benchmark contract `v6`.

Active defaults:
- starting bankroll: `10.0`
- position size fraction: `1.0`
- game limit: `100`
- overlap policy: one position at a time
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

## Current Validated Family Status
Validated on `2026-04-20` against season `2025-26`, phase `regular_season`, starting bankroll `$10.00`, position fraction `1.0`, and the first `100` chronological games.

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
  - mean ending bankroll `295,209.99`
  - median ending bankroll `141,733.35`
  - ending bankroll range `45,249.66` to `1,626,989.21`
  - worst drawdown `52.38%`

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
- `underdog_liftoff`
- `winner_definition`

Current result:
- full-sample ending bankroll `14,492.75`
- full-sample max drawdown `28.15%`
- full-sample executed trades `59`
- full-sample skipped overlaps `92`

Interpretation:
- useful as a diversification surface
- still weaker than the top standalone families because overlap collisions suppress the highest-upside paths

## Statistical Routing
Current band map:
- `10-20`: `underdog_liftoff`
- `20-30`: `underdog_liftoff`
- `30-40`: `inversion`
- `40-50`: `inversion`
- `50-60`: `winner_definition`
- `60-70`: `winner_definition`
- `70-80`: `winner_definition`
- `80-90`: `winner_definition`
- `90-100`: `winner_definition`

Current routed result:
- full-sample ending bankroll `19,541.50`
- full-sample max drawdown `30.66%`
- full-sample executed trades `52`
- full-sample skipped overlaps `66`

Interpretation:
- opening band is enough to support deterministic family routing
- the routed sleeve now prefers `underdog_liftoff` in the two lowest active bands
- routed performance is still below the strongest standalone families, so the next step is allocation and overlap logic rather than new threshold churn

## Rejected Refinements
Rejected or superseded variants from the refinement pass:
- `inversion` low-open cut expansion above `30c`
- `winner_definition` stronger break-line variants
- weaker `underdog_liftoff` trigger zones around `38c` and wider `4c` stops

## Promotion Rule
A family is not promoted on win rate alone.

Promotion requires:
- positive full-sample sequential result
- positive time-validation and random-holdout benchmark signal
- acceptable drawdown
- repeated-seed robustness that remains directionally defensible
