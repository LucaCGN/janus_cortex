# Sequential Portfolio Benchmarking Reference

## Purpose
Define the canonical bankroll-simulation contract for the NBA analysis module and record the current validated outcomes for:
- single-family sequential replay
- repeated-seed robustness across the full five-family set
- the first combined keep-family sleeve

## Canonical Contract
Sequential portfolio benchmarking now extends `run_analysis_backtests` under benchmark contract `v4`.

Active defaults:
- starting bankroll: `10.0`
- position size fraction: `1.0`
- game limit: `100`
- overlap policy: one position at a time, later entries are skipped while capital is already committed
- split set:
  - `full_sample`
  - `time_train`
  - `time_validation`
  - `random_train`
  - `random_holdout`

Final validation sweep for this pass used the explicit robustness seeds:
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
- trade order is sorted by `entry_at`, then stable game, side, family, and state-index tie-breakers
- the game window is the first `100` chronological games in the chosen sample
- the same sample and seed produce the same bankroll path

## Portfolio Artifacts
Each benchmarked backtest run emits:
- `benchmark_portfolio_summary`
- `benchmark_portfolio_steps`
- `benchmark_portfolio_candidate_freeze`
- `benchmark_portfolio_robustness_detail`
- `benchmark_portfolio_robustness_summary`

Key portfolio metrics:
- `ending_bankroll`
- `total_pnl_amount`
- `compounded_return`
- `max_drawdown_amount`
- `max_drawdown_pct`
- `executed_trade_count`
- `skipped_overlap_count`
- `skipped_bankroll_count`

## Current Validated Family Status
Validated on `2026-04-20` against season `2025-26`, phase `regular_season`, with a `$10.00` starting bankroll and `1.0` position fraction.

### `inversion`
- how it works:
  - buy the underdog once its in-game price first crosses above `50c`
  - exit if it breaks back below `50c` or the game ends
- full-sample result:
  - ending bankroll: `790514.77`
  - max drawdown: `41.12%`
  - executed trades: `62`
- 10-seed robustness:
  - label: `stable_positive`
  - positive seeds: `10 / 10`
  - ending bankroll range: `1964.71` to `143771145.05`
  - median ending bankroll: `306157.58`
  - worst drawdown: `52.35%`

### `winner_definition`
- how it works:
  - buy once the market reaches `80c`
  - exit if it breaks below `75c` or the game ends
- full-sample result:
  - ending bankroll: `2359.91`
  - max drawdown: `24.49%`
  - executed trades: `50`
- 10-seed robustness:
  - label: `stable_positive`
  - positive seeds: `10 / 10`
  - ending bankroll range: `46877.16` to `1822024.11`
  - median ending bankroll: `120694.72`
  - worst drawdown: `37.71%`

### `reversion`
- how it works:
  - buy a strong favorite after a `10c` drawdown from the opening price
  - exit on reclaim to `open - 2c` or the end of game
- full-sample result:
  - ending bankroll effectively goes to zero
  - max drawdown: `100.00%`
- 10-seed robustness:
  - label: `stable_negative`
  - positive seeds: `0 / 10`
  - median ending bankroll: effectively `0`
  - worst drawdown: `100.00%`

### `comeback_reversion`
- how it works:
  - buy an underdog in `Q2` or `Q3` when it trails by `5+`, still trades in the `15c-40c` band, and recent scoring momentum turns positive
  - exit at `+8c`, `-6c`, or the end of game
- full-sample result:
  - ending bankroll: `0.0187`
  - max drawdown: `99.84%`
- 10-seed robustness:
  - label: `mixed`
  - positive seeds: `1 / 10`
  - ending bankroll range: `0.0006` to `26.37`
  - median ending bankroll: `0.6487`
  - worst drawdown: `99.99%`

### `volatility_scalp`
- how it works:
  - buy a mid-band team in `Q1` after a `12c` drop from the opening price
  - exit on a partial reclaim, a `-5c` stop, or the end of game
- full-sample result:
  - ending bankroll: `0.0034`
  - max drawdown: `99.97%`
- 10-seed robustness:
  - label: `mixed`
  - positive seeds: `1 / 10`
  - ending bankroll range: `1.19` to `11.34`
  - median ending bankroll: `2.62`
  - worst drawdown: `88.14%`

## Combined Keep-Family Sleeve
The first combined sleeve merges the two surviving single-family candidates:
- members: `inversion,winner_definition`
- combined family label: `combined_keep_families`

Current result:
- full-sample ending bankroll: `17867.59`
- full-sample compounded return: `1785.76`
- full-sample max drawdown: `25.36%`
- full-sample executed trades: `56`
- full-sample skipped overlaps: `113`
- random-holdout ending bankroll: `28604419.86`
- random-holdout compounded return: `2860440.99`
- random-holdout max drawdown: `24.48%`

Interpretation:
- the combined sleeve improves drawdown versus pure `inversion`
- it does not beat pure `inversion` on full-sample terminal bankroll because family collisions remove many `inversion` entries
- the next statistical question is allocation and priority, not whether the sleeve exists at all

## Final Improvement Pass
The retained improvement from this pass is methodological, not a live strategy-math change:
- repeated-seed robustness now covers all five families instead of only the keep set
- the final evidence set is a 10-seed bankroll sweep on the same `$10` portfolio contract

Tested and rejected during this pass:
- `winner_definition`
  - tested variant: require a two-state confirmation after `80c`
  - result: lower terminal bankroll than the original `reach_80c` rule on the full sample and on every tested holdout seed

Decision:
- keep the older `winner_definition` rule as the active production family definition
- treat later trigger refinements as explicit experiments, not silent default replacements

## Historical Rejected Threshold Experiment
Tested and rejected on `2026-04-20`:
- `inversion`
  - tested variant: `52c` entry / `48c` exit
  - result: worse sequential compounding than the original `50c/50c` rules
- `winner_definition`
  - tested variant: `82c` entry / `77c` exit
  - result: worse sequential compounding than the original `80c/75c` rules

## Promotion Rule
Per-trade average return is not enough to promote a family.

Promotion now requires:
- positive sequential full-sample result
- positive sequential random-holdout result
- stable repeated-seed behavior
- tolerable drawdown under the same bankroll contract

## Statistical And LLM Boundary
The LLM layer is not part of the benchmark contract.

What remains purely statistical:
- keep/drop decisions
- repeated-seed robustness
- drawdown and tail-risk comparisons
- combined-sleeve inclusion and future weight selection

Where a later statistical-plus-LLM review layer may help:
- explain why a family is robust but collision-heavy
- summarize recurring failure traces near the worst-seed cases
- propose the next parameter families to test after the statistical gate identifies the weak boundary

## Next Follow-On Questions
- should the combined sleeve itself get repeated-seed robustness before allocation work starts?
- should the next lane test explicit weighting and priority rules inside `combined_keep_families`?
- should read-only visualization surface the sequential steps, overlap skips, and seed-robustness tables directly?
- where should statistical gating hand off to a downstream LLM reviewer for interpretation only, not scoring?
