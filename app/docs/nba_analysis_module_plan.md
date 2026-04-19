# NBA Analysis Module Plan

## Why / Revision Note
- Added after the 2025-26 regular season closed to convert the now-complete regular-season dataset into an offline research and backtesting lane.
- This module is intentionally mart-first and report-first, not live-strategy-first.

## Objective
- Build a reproducible research stack for regular-season NBA odds analysis on top of persisted games, play-by-play, linked Polymarket events, and outcome price ticks.
- Keep v1 focused on `2025-26` `regular_season` only.
- Defer player-impact, injury causality, and public HTTP routes until the mart and baselines stabilize.

## Primary Files / Areas
- `app/data/pipelines/daily/nba/analysis_module.py`
- `app/data/databases/migrations/0018_v1_0_1__nba_analysis_mart.sql`
- `tests/app/data/pipelines/daily/nba/test_analysis_module_pytest.py`
- `tests/app/data/databases/test_postgres_migrations_pytest.py`
- `app/docs/nba_analysis_data_products.md`
- `app/docs/nba_analysis_modeling_and_backtesting.md`
- `app/docs/nba_analysis_parallel_workstreams.md`

## Scope In
- Canonical research universe for finished regular-season games.
- Versioned analysis mart tables under `nba`.
- Offline report generation from mart outputs only.
- Offline backtesting families using mart state rows only.
- Interpretable baseline modeling outputs with time-based validation.
- JSON, Markdown, CSV, and optional parquet artifacts under `output/nba_analysis`.

## Scope Out
- Play-in, playoffs, preseason, and unfinished postseason games in v1.
- Injury / availability causal layer.
- Player-impact modeling beyond what is implicitly retained in saved play-by-play payloads.
- Public REST routes or live game decision endpoints.
- LLM-in-the-loop trade logic.

## Data Inputs
- `nba.nba_games`
- `nba.nba_game_feature_snapshots`
- `nba.nba_game_event_links`
- `nba.nba_play_by_play`
- `market_data.outcome_price_ticks`
- `catalog.events`
- `catalog.markets`
- `catalog.outcomes`
- `nba.nba_team_stats_snapshots`
- `nba.nba_player_stats_snapshots`
- `nba.nba_team_insights`

## Target Questions
- Which teams most frequently outperformed or underperformed opening expectation?
- Which teams and opening bands produced the largest intragame swings?
- Which teams crossed the 50c inversion line most often?
- Which favorite profiles repeatedly opened rebound windows for underdogs?
- Which game contexts produced the highest reversion or inversion probability?
- At what thresholds did eventual winners stop meaningfully reopening?
- How did opening expectation drift by team across the season?

## Module Order
1. Research universe and QA gate
2. Core analysis mart
3. Descriptive report pack
4. Backtesting families
5. Predictive baselines
6. Only later, optional read-only serving or LLM consumption

## Validation Rules
- Use only `game_status = 3` regular-season games.
- Treat `covered_pre_and_ingame` as the full research corpus.
- Keep `pregame_only`, `covered_partial`, and `no_matching_event` visible in QA outputs but out of full state-panel backtests and model training.
- Mart outputs must be idempotent for the same `season`, `season_phase`, and `analysis_version`.
- Reports, backtests, and models must consume mart outputs, not ad hoc raw SQL directly from ingest tables.

## Exit Criteria
- Mart build completes for the target season and writes all five analysis tables.
- Report pack renders from mart tables only.
- Backtests run from mart state rows only and emit stable artifact files.
- Baseline model command runs and emits metrics or explicit `insufficient_data` status without failing.
- Schema, guide, and checkpoint docs are synchronized.

## Implementation Checklist
- [x] Add mart migration under `nba`.
- [x] Add offline mart / report / backtest / model CLI module.
- [x] Add dedicated pytest coverage for mart behavior.
- [x] Add planning docs for scope, data products, and modeling policy.
- [ ] Promote this lane into broader operator workflows once results are validated.

## Test Checklist
- [x] Migration inventory test updated for new mart tables.
- [x] Dedicated analysis-module pytest added.
- [ ] Full-season mart build validation on live DB.
- [ ] Hand-check known Lakers / OKC style fixtures once selected and frozen.

## Artifact Sync Requirements
- Keep `app/docs/development_guide.md` aligned with the offline-first research-lane note.
- Keep `app/docs/scalable_db_schema_proposal.md` aligned with migration `0018_v1_0_1__nba_analysis_mart.sql`.
- Keep `dev-checkpoint/README.md` and `dev-checkpoint/v1.0.1.md` aligned with the phase status of this analysis lane.
