# Analysis Sampling And Benchmarking Reference

## Purpose
Freeze the benchmark contract that promotes the strategy lab into a reproducible multi-algorithm experiment program.

This reference covers:
- which benchmark splits exist
- which comparators every family is scored against
- which comparative artifacts the backtest runner now emits
- how the first `keep`, `drop`, and `experimental` labels are derived

## Benchmark Contract
`run_analysis_backtests` remains the canonical CLI entry point, but it now evaluates strategies under one shared benchmark contract:

- full sample
- time-train sample
- time-validation sample
- random-train sample
- random-holdout sample

The benchmark contract version is `v1`.

## Shared Metrics
Every strategy family is scored with the same metric set:
- `trade_count`
- `win_rate`
- `avg_gross_return`
- `median_gross_return`
- `avg_gross_return_with_slippage`
- `avg_hold_time_seconds`
- `avg_mfe_after_entry`
- `avg_mae_after_entry`

## Comparator Baselines
Each family and sample is compared against:

### `no_trade`
- zero-return baseline on the same opportunity count

### `winner_prediction_hold_to_end`
- buy the strategy-selected side at the strategy entry
- hold until the last observed state of that game-side path
- use the same slippage setting as the strategy run

This keeps timing-sensitive strategies comparable against a naive "take the side and hold it" baseline.

## Split Policy
### Time Validation
- reuses the same game-date cutoff logic as the modeling package
- default cutoff is the 75th-percentile game date unless explicitly provided

### Random Holdout
- operates at `game_id` level to keep all states from the same game in the same random split
- is reproducible from `holdout_seed`
- defaults to a 10% holdout ratio

## Comparative Artifacts
The backtest runner now emits:
- full-sample family artifacts from the strategy-lab release
- `benchmark_split_summary`
- `benchmark_family_summary`
- `benchmark_comparator_summary`
- `benchmark_sample_vs_full`
- `benchmark_context_rankings`
- `benchmark_candidate_freeze`
- `benchmark_experiment_registry.json`

## Candidate Freeze Labels
The first benchmark labels are intentionally conservative:

### `keep`
- full sample meets the minimum trade count
- time-validation sample has trades
- random-holdout sample has trades
- full, time-validation, and random-holdout average slippage-adjusted returns are all positive

### `drop`
- full sample meets the minimum trade count
- time-validation and random-holdout samples both exist
- full, time-validation, and random-holdout average slippage-adjusted returns are all non-positive

### `experimental`
- below minimum trade count
- missing validation or holdout trades
- or mixed benchmark signals

## Operator Note
This benchmark lane does not pick a live strategy. It produces the first disciplined comparison surface so the next adapter and frontend branches can consume stable results instead of ad hoc backtest logs.
