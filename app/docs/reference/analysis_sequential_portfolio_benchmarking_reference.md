# Sequential Portfolio Benchmarking Reference

## Purpose
Define the canonical bankroll-simulation contract for the NBA analysis module and record the current validated strategy outcomes under that contract.

This reference answers:
- how sequential portfolio replay is scored
- which artifacts are emitted
- which active families currently survive the bankroll lens
- which tuning experiments were rejected

## Canonical Contract
Sequential portfolio benchmarking now extends `run_analysis_backtests` under benchmark contract `v2`.

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

Determinism rules:
- trade order is sorted by `entry_at`, then stable game and side tie-breakers
- the game window is the first `100` chronological games in the chosen sample
- the same sample and seed produce the same bankroll path

## New Portfolio Artifacts
Each backtest run now emits:
- `benchmark_portfolio_summary`
- `benchmark_portfolio_steps`
- `benchmark_portfolio_candidate_freeze`

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
- sequential result:
  - full-sample bankroll effectively goes to zero
  - random-holdout bankroll effectively goes to zero
- reason:
  - tail losses dominate compounding despite the high hit rate

### `comeback_reversion`
- active rules unchanged
- status: `drop`
- sequential result:
  - full-sample bankroll: `0.0187`
  - random-holdout bankroll: `0.0076`
- reason:
  - the family does not survive bankroll carry-forward and needs a redesign, not micro-tuning

### `volatility_scalp`
- active rules unchanged
- status: `drop`
- sequential result:
  - full-sample bankroll: `0.0034`
  - random-holdout bankroll: `3.3017`
- reason:
  - narrow scalp behavior does not produce a durable portfolio sleeve

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

## Interpretation Rule
Per-trade average return is no longer enough to promote a family.

Promotion now requires:
- positive sequential full-sample result
- positive sequential random-holdout result
- tolerable drawdown under the same bankroll contract

## Next Follow-On Questions
- how stable are the `keep` families across repeated holdout seeds and resample windows?
- should bankroll sizing stay full-capital or move to a capped fraction rule for drawdown control?
- when do we start blending multiple surviving families into one combined sleeve instead of replaying them independently?
