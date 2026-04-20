# Analysis Consumer Adapters Reference

## Purpose
Freeze the read-only consumer contract that downstream branches should use instead of reading raw nested artifact files or raw ingest tables directly.

This reference covers:
- the adapter entry points
- how analysis version resolution works
- what the normalized consumer snapshot contains
- what the frontend branch should treat as stable input

## Stable Entry Points
Main helpers under `app.data.pipelines.daily.nba.analysis.consumer_adapters`:

- `list_available_analysis_versions(...)`
- `resolve_analysis_consumer_paths(request)`
- `load_analysis_consumer_bundle(request)`
- `build_analysis_consumer_snapshot(bundle)`
- `load_analysis_consumer_snapshot(request)`

Request contract:
- `AnalysisConsumerRequest`
  - `season`
  - `season_phase`
  - `analysis_version`
  - `backtest_experiment_id`
  - `output_root`

## Version Resolution
If `analysis_version` is omitted:
- the adapter lists version directories under `DEFAULT_OUTPUT_ROOT / season / season_phase`
- versions are sorted by numeric tokens
- the latest available version is selected

If `backtest_experiment_id` is provided:
- the adapter verifies that the resolved backtest artifact matches it
- mismatches raise an error instead of silently loading the wrong benchmark run

## Required Artifact Inputs
The adapter expects the canonical JSON artifacts:
- `analysis_report.json`
- `backtests/run_analysis_backtests.json`
- `models/train_analysis_baselines.json`

These are treated as the stable consumer inputs for:
- frontend
- later LLM-facing orchestration
- read-only operator surfaces

## Normalized Snapshot Shape
`load_analysis_consumer_snapshot` returns one stable payload with:

### Metadata
- `season`
- `season_phase`
- `analysis_version`
- `output_dir`
- normalized `artifacts`

### Report Surface
- `report.universe`
- `report.section_order`
- `report.sections`

Each section includes:
- `key`
- `title`
- `columns`
- `row_count`
- `rows`

### Benchmark Surface
- `benchmark.contract_version`
- `benchmark.minimum_trade_count`
- `benchmark.experiment`
- `benchmark.strategy_rankings`
- `benchmark.candidate_freeze`
- `benchmark.split_summary`
- `benchmark.comparators`
- `benchmark.comparator_summary`
- `benchmark.context_rankings`

`strategy_rankings` is the primary frontend-ready leaderboard:
- based on `full_sample`
- enriched with candidate-freeze labels and reasons
- sorted by slippage-adjusted return and trade count

### Model Surface
- `models.feature_set_version`
- `models.train_cutoff`
- `models.validation_window`
- `models.tracks`

Each track includes:
- `track_name`
- `status`
- `model_family`
- `train_rows`
- `validation_rows`
- `metrics`
- `naive_comparison`
- normalized target summaries where applicable

## Contract Rule
Consumers should not:
- guess file names from raw nested artifacts
- read raw ingest tables
- infer ranking logic independently

Consumers should:
- use the normalized snapshot
- treat missing required artifacts as a load failure
- pass explicit `analysis_version` or `backtest_experiment_id` when reproducibility matters

## Frontend Handoff
The frontend branch should build on:
- `load_analysis_consumer_snapshot` for read-only page data
- the adapter artifact map for deep links to CSV, markdown, or JSON outputs
- the benchmark leaderboard and model track summaries as the initial stable UI contract
