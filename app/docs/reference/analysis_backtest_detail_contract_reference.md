# Analysis Backtest Detail Contract Reference

## Purpose
Define the stable read-only helper surface for per-family benchmark comparison detail.

This contract exists so downstream consumers can inspect family-level benchmark outputs without parsing raw backtest artifact bundles themselves.

## Helper Surface
- `build_analysis_backtest_index(bundle)`
- `load_analysis_backtest_index(request)`
- `build_analysis_backtest_family_detail(bundle, strategy_family=..., trade_limit=..., context_limit=..., trace_limit=...)`
- `load_analysis_backtest_family_detail(request, strategy_family=..., trade_limit=..., context_limit=..., trace_limit=...)`

The request shape stays aligned with `AnalysisConsumerRequest`.

## Current Payload Scope

### Backtest Index
- season identity
- resolved analysis version and output directory
- summary benchmark payload from the consumer snapshot
- one family row per ranked strategy family
- normalized per-family artifact paths for:
  - trades
  - best trades
  - worst trades
  - context summary
  - trade traces

### Backtest Family Detail
- season identity
- resolved analysis version and output directory
- selected `strategy_family`
- ranked full-sample summary row
- sample-level family summaries
- candidate-freeze row
- comparator summary rows
- context ranking rows
- normalized artifact paths
- bounded best-trade rows
- bounded worst-trade rows
- bounded context-summary rows
- bounded trade traces

## Dependency Rules
- this contract is read-only
- it may read only the versioned benchmark artifacts already emitted by `run_analysis_backtests`
- it must not add new benchmark computation or schema changes
- frontend code should consume this helper surface rather than re-implementing family artifact parsing

## Current Artifact Expectations
When present, the contract reads:
- `family_summary.csv/parquet`
- `<family>_trades.csv/parquet`
- `<family>_best_trades.csv/parquet`
- `<family>_worst_trades.csv/parquet`
- `<family>_context_summary.csv/parquet`
- `<family>_trade_traces.json`

## Bounds
- `trade_limit` bounds best and worst trade previews
- `context_limit` bounds context summary previews
- `trace_limit` bounds trace previews

These bounds are consumer-facing safety limits, not benchmark computation settings.
