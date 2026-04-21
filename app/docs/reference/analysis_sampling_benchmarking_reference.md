# Analysis Sampling And Benchmarking Reference

## Purpose
Freeze the benchmark contract that promotes the strategy lab into a reproducible multi-family experiment program.

This reference covers:
- benchmark splits
- shared comparators
- emitted artifacts
- keep/drop labeling
- routing selection policy
- robustness summary fields

## Benchmark Contract
`run_analysis_backtests` is the canonical CLI entry point.

The active benchmark contract version is `v6`.

Shared samples:
- `full_sample`
- `time_train`
- `time_validation`
- `random_train`
- `random_holdout`

## Shared Metrics
Every strategy family is scored with:
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
- `no_trade`
- `winner_prediction_hold_to_end`

## Split Policy
### Time Validation
- reuses the same game-date cutoff logic as the modeling package
- default cutoff remains the 75th-percentile game date unless explicitly provided

### Random Holdout
- operates at `game_id` level
- is reproducible from `holdout_seed`
- defaults to a 10% holdout ratio

## Comparative Artifacts
The backtest runner emits:
- family-level artifacts
- `benchmark_split_summary`
- `benchmark_family_summary`
- `benchmark_comparator_summary`
- `benchmark_sample_vs_full`
- `benchmark_context_rankings`
- `benchmark_candidate_freeze`
- `benchmark_route_summary`
- `benchmark_portfolio_summary`
- `benchmark_portfolio_steps`
- `benchmark_portfolio_candidate_freeze`
- `benchmark_portfolio_robustness_detail`
- `benchmark_portfolio_robustness_summary`
- `benchmark_experiment_registry.json`

## Candidate Freeze Labels
### `keep`
- meets minimum trade count on full sample
- has time-validation and random-holdout trades
- full, time-validation, and random-holdout average slippage-adjusted returns are all positive

### `drop`
- meets minimum trade count on full sample
- has time-validation and random-holdout trades
- full, time-validation, and random-holdout average slippage-adjusted returns are all non-positive

### `experimental`
- below minimum trade count
- missing validation or holdout trades
- or mixed benchmark signals

## Routing Layer
The current routing lane is deterministic:
- learn an opening-band family map from `time_train`
- prefer the highest positive average slippage-adjusted return in-band
- fall back to the best available average return if no positive family exists for that band
- replay that band map across every split as `statistical_routing_v1`

Current promoted band map:
- `10-20`: `underdog_liftoff`
- `20-30`: `underdog_liftoff`
- `30-40`: `inversion`
- `40-50`: `inversion`
- `50-60`: `winner_definition`
- `60-70`: `winner_definition`
- `70-80`: `winner_definition`
- `80-90`: `winner_definition`
- `90-100`: `winner_definition`

## Robustness Summary Fields
`benchmark_portfolio_robustness_summary` now includes:
- `positive_seed_rate`
- `min_ending_bankroll`
- `mean_ending_bankroll`
- `median_ending_bankroll`
- `max_ending_bankroll`
- `min_compounded_return`
- `mean_compounded_return`
- `median_compounded_return`
- `max_compounded_return`
- `worst_max_drawdown_pct`

This makes the average 100-game ending bankroll a first-class artifact rather than an ad hoc post-processing step.

## Frozen Current Benchmark Outcome
Keep:
- `inversion`
- `winner_definition`
- `underdog_liftoff`

Drop:
- `reversion`
- `comeback_reversion`
- `volatility_scalp`

## Operator Note
This lane remains statistical and deterministic. It does not choose live trades.

Any later LLM usage should stay interpretive and consume these artifacts rather than replacing the benchmark contract.
