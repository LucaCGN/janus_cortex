# Sequential Portfolio Benchmarking Reference

## Purpose
Define the canonical bankroll-simulation contract for the NBA analysis module and record the current validated outcomes for:
- single-family sequential replay
- repeated-seed robustness on surviving families
- the first combined keep-family sleeve

## Canonical Contract
Sequential portfolio benchmarking now extends `run_analysis_backtests` under benchmark contract `v3`.

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
- repeated-seed robustness set:
  - `1107`
  - `2113`
  - `3251`
  - `4421`
  - `5573`

Determinism rules:
- trade order is sorted by `entry_at`, then stable game, side, family, and state-index tie-breakers
- the game window is the first `100` chronological games in the chosen sample
- the same sample and seed produce the same bankroll path

## Portfolio Artifacts
Each benchmarked backtest run now emits:
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
Validated on `2026-04-20` against season `2025-26`, phase `regular_season`, with random-holdout seed `1107`.

### `inversion`
- active rules:
  - entry: `first_cross_above_50c`
  - exit: `break_back_below_50c_or_end`
- status: `keep`
- current sequential result:
  - full-sample ending bankroll: `790514.77`
  - full-sample max drawdown: `41.12%`
  - random-holdout ending bankroll: `377435.94`
  - random-holdout max drawdown: `30.94%`

### `winner_definition`
- active rules:
  - entry: `reach_80c`
  - exit: `break_75c_or_end`
- status: `keep`
- current sequential result:
  - full-sample ending bankroll: `2359.91`
  - full-sample max drawdown: `24.49%`
  - random-holdout ending bankroll: `213303.89`
  - random-holdout max drawdown: `29.81%`

### `reversion`
- active rules unchanged
- status: `drop`
- reason:
  - tail losses dominate compounding despite the high hit rate

### `comeback_reversion`
- active rules unchanged
- status: `drop`
- reason:
  - the family does not survive bankroll carry-forward and needs a redesign, not micro-tuning

### `volatility_scalp`
- active rules unchanged
- status: `drop`
- reason:
  - narrow scalp behavior does not produce a durable portfolio sleeve

## Combined Keep-Family Sleeve
The first combined sleeve now merges the two surviving single-family candidates:
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

## Repeated-Seed Robustness
Repeated-seed robustness currently runs only on the single-family `keep` set.

### `inversion`
- robustness label: `stable_positive`
- positive seeds: `5 / 5`
- ending bankroll range: `1964.71` to `11870707.53`
- median ending bankroll: `377435.94`
- executed-trade range: `46` to `62`
- worst max drawdown: `46.69%`

### `winner_definition`
- robustness label: `stable_positive`
- positive seeds: `5 / 5`
- ending bankroll range: `55976.44` to `213303.89`
- median ending bankroll: `102762.64`
- executed-trade range: `90` to `103`
- worst max drawdown: `37.71%`

Interpretation:
- both surviving families remain profitable across every tested seed
- `inversion` has much wider dispersion and higher upside, but also the higher worst-seed drawdown
- `winner_definition` remains the steadier sleeve under repeated holdout reshuffles

## Rejected Threshold Experiment
Tested and rejected on `2026-04-20`:
- `inversion`
  - tested variant: `52c` entry / `48c` exit
  - result: worse sequential compounding than the original `50c/50c` rules
- `winner_definition`
  - tested variant: `82c` entry / `77c` exit
  - result: worse sequential compounding than the original `80c/75c` rules

Decision:
- keep the older defaults as the active production family definitions
- treat later threshold work as explicit experiments, not silent default replacements

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
