# NBA Analysis Data Products

## Why / Revision Note
- This document fixes the mart grain and rebuild semantics before further analytics work expands.

## Objective
- Define the reusable derived tables that convert raw game, play-by-play, and odds coverage into stable research objects.

## Primary Files / Areas
- `app/data/databases/migrations/0018_v1_0_1__nba_analysis_mart.sql`
- `app/data/pipelines/daily/nba/analysis_module.py`

## Data Product Inventory

### `nba.nba_analysis_game_team_profiles`
- Grain: `game_id x team_side x analysis_version`
- Purpose: one per-team view of opening expectation, intragame path shape, inversion behavior, and winner-stability timestamps.
- Core keys:
  - `game_id`
  - `team_side`
  - `season`
  - `season_phase`
  - `analysis_version`
- Core measures:
  - `opening_price`, `closing_price`
  - `pregame_price_range`, `ingame_price_range`, `total_swing`
  - `max_favorable_excursion`, `max_adverse_excursion`
  - `inversion_count`, `first_inversion_at`
  - `seconds_above_50c`, `seconds_below_50c`
  - `winner_stable_70/80/90/95_*`
- Filters:
  - include all finished regular-season games in the universe
  - `research_ready_flag` separates the full research corpus from descriptive-only rows

### `nba.nba_analysis_state_panel`
- Grain: `game_id x team_side x state_index x analysis_version`
- Purpose: aligned event-time state rows for backtests and model baselines.
- Core keys:
  - `game_id`
  - `team_side`
  - `state_index`
  - `season`
  - `season_phase`
  - `analysis_version`
- Core context:
  - `event_at`, `period`, `period_label`, `clock_elapsed_seconds`, `seconds_to_game_end`
  - `score_for`, `score_against`, `score_diff`, `score_diff_bucket`, `context_bucket`
  - `team_led_flag`, `market_favorite_flag`, `scoreboard_control_mismatch_flag`
- Core market features:
  - `team_price`, `opening_price`, `price_delta_from_open`, `price_mode`
  - `gap_before_seconds`, `gap_after_seconds`
- Forward labels:
  - `mfe_from_state`, `mae_from_state`
  - `large_swing_next_12_states_flag`
  - `crossed_50c_next_12_states_flag`
  - `winner_stable_70/80/90/95_after_state_flag`
- Filters:
  - only build for `research_ready_flag = true`

### `nba.nba_analysis_team_season_profiles`
- Grain: `team_id x season x season_phase x analysis_version`
- Purpose: season-level team volatility and expectation profiles.
- Core measures:
  - averages for opening / closing / range / swing / MFE / MAE
  - inversion rates
  - favorite drawdown and underdog spike behavior
  - scoreboard-control mismatch rate
  - opening-price trend slope
  - rolling 10-game and 20-game JSON summaries

### `nba.nba_analysis_opening_band_profiles`
- Grain: `season x season_phase x opening_band x analysis_version`
- Purpose: summarize how different opening-probability bands behave.
- Default bins:
  - `0-10`, `10-20`, ..., `90-100`

### `nba.nba_analysis_winner_definition_profiles`
- Grain: `season x season_phase x threshold_cents x context_bucket x analysis_version`
- Purpose: summarize when eventual winners become stably defined in specific game contexts.
- Default thresholds:
  - `70`
  - `80`
  - `90`
  - `95`

## Versioning
- Every analysis table carries:
  - `season`
  - `season_phase`
  - `analysis_version`
  - `computed_at`
- `analysis_version` is the rebuild boundary.
- The initial mart release uses `v1_0_1`.

## Rebuild Semantics
- `build_analysis_mart --rebuild` deletes existing mart rows for the same `season`, `season_phase`, and `analysis_version` before rebuilding.
- Partial rebuilds replace only the targeted game ids in the per-game tables, then recompute the aggregate season tables for the same version.
- Reports, backtests, and models should read from the exact mart version requested, never mix versions implicitly.

## QA Expectations
- `research_ready_flag` implies:
  - finished regular-season game
  - `coverage_status = covered_pre_and_ingame`
  - aligned timed play-by-play rows for both sides
  - selected market outcome series available for both sides
- `price_path_reconciled_flag` is stricter:
  - both sides fully aligned
  - closing path direction agrees with the final winner

## Artifact Sync Requirements
- When grains or keys change, update:
  - `app/docs/nba_analysis_module_plan.md`
  - `app/docs/nba_analysis_modeling_and_backtesting.md`
  - `app/docs/scalable_db_schema_proposal.md`
