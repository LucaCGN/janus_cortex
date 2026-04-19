# NBA Analysis Parallel Workstreams

## Why / Revision Note
- Added after the regular-season mart was restored and built so the `v1.0.1` analysis lane can be split into parallel implementation packets without merge collisions.
- The current bottleneck is structural: too much mart, report, backtest, and modeling logic still sits inside one module.

## Objective
- Convert the existing analysis-module plan into concrete subagent-ready workstreams.
- Define write ownership, dependencies, acceptance checks, and launch order.
- Keep the lane offline-first, mart-first, and regular-season-only for `v1`.

## Current Baseline Snapshot
- Season scope:
  - `2025-26`
  - `regular_season`
- Current restored corpus on `2026-04-19`:
  - finished games: `1224`
  - linked Polymarket events: `1222`
  - feature snapshots: `1224`
  - `covered_pre_and_ingame`: `1209`
  - coverage residuals:
    - `no_history=10`
    - `covered_partial=2`
    - `pregame_only=1`
    - `no_matching_event=2`
- Current mart status:
  - `nba.nba_analysis_game_team_profiles=2448`
  - `nba.nba_analysis_state_panel=1388443`
- Existing CLI surface already present:
  - `build_analysis_mart`
  - `build_analysis_report`
  - `run_analysis_backtests`
  - `train_analysis_baselines`

## Parallelization Rules
- One workstream owns each write scope.
- No workstream should edit raw ingest pipelines unless its packet explicitly says so.
- Reports, backtests, and models must consume mart outputs only.
- If a workstream needs a helper from another lane, add it through the owned interface, not by editing another lane's private module.
- Each workstream must add or update dedicated pytest coverage in its owned area.
- Keep `app/data/pipelines/daily/nba/analysis_module.py` as a compatibility entrypoint until the split stabilizes.

## Dependency Graph
1. `A0` contracts and package split
2. `A1`, `A2`, `A3` in parallel after `A0`
3. `A4`, `A5`, `A6`, `A7` in parallel after `A1-A3`
4. `A8` only after `A4-A6` stabilize

## Workstream Map

| ID | Workstream | Starts After | Owns | Main Output |
| --- | --- | --- | --- | --- |
| `A0` | Contracts and package split | none | module boundaries | stable package layout |
| `A1` | Research universe and QA gate | `A0` | universe filters and completeness rules | canonical universe artifact |
| `A2` | Game and season mart profiles | `A0` | game-team and season aggregates | team-level mart tables |
| `A3` | State panel and context mart | `A0` | event-time panel and winner-definition profiles | state-level mart tables |
| `A4` | Descriptive report pack | `A1-A3` | report builders and exports | markdown/json/csv research pack |
| `A5` | Backtest engine | `A1-A3` | strategy engine and trade outputs | reversion/inversion/winner-definition backtests |
| `A6` | Predictive baselines | `A1-A3` | baseline features, train/validate, metrics | offline model artifacts |
| `A7` | Player-impact shadow lane | `A3` | non-blocking player/absence research | player-impact research artifact |
| `A8` | Consumer adapters | `A4-A6` | read-only consumers for dashboard or later LLM use | stable downstream adapter layer |

## Package Target
- Keep `app/data/pipelines/daily/nba/analysis_module.py` as a thin compatibility wrapper.
- Split the internal implementation into:
  - `app/data/pipelines/daily/nba/analysis/__init__.py`
  - `app/data/pipelines/daily/nba/analysis/contracts.py`
  - `app/data/pipelines/daily/nba/analysis/artifacts.py`
  - `app/data/pipelines/daily/nba/analysis/cli.py`
  - `app/data/pipelines/daily/nba/analysis/universe.py`
  - `app/data/pipelines/daily/nba/analysis/mart_game_profiles.py`
  - `app/data/pipelines/daily/nba/analysis/mart_state_panel.py`
  - `app/data/pipelines/daily/nba/analysis/mart_aggregates.py`
  - `app/data/pipelines/daily/nba/analysis/reports.py`
  - `app/data/pipelines/daily/nba/analysis/backtests/engine.py`
  - `app/data/pipelines/daily/nba/analysis/backtests/reversion.py`
  - `app/data/pipelines/daily/nba/analysis/backtests/inversion.py`
  - `app/data/pipelines/daily/nba/analysis/backtests/winner_definition.py`
  - `app/data/pipelines/daily/nba/analysis/models/features.py`
  - `app/data/pipelines/daily/nba/analysis/models/volatility.py`
  - `app/data/pipelines/daily/nba/analysis/models/trade_quality.py`
  - `app/data/pipelines/daily/nba/analysis/models/winner_timing.py`
  - `app/data/pipelines/daily/nba/analysis/player_impact.py`

## Execution Packets

### `A0` Contracts And Package Split
#### Objective
- Remove the current merge hotspot by splitting `analysis_module.py` into an internal package with stable interfaces and no intended behavior change.

#### Owned Files
- `app/data/pipelines/daily/nba/analysis_module.py`
- `app/data/pipelines/daily/nba/analysis/__init__.py`
- `app/data/pipelines/daily/nba/analysis/contracts.py`
- `app/data/pipelines/daily/nba/analysis/artifacts.py`
- `app/data/pipelines/daily/nba/analysis/cli.py`

#### Do Not Touch
- Mart SQL migration semantics
- Dashboard service modules
- Raw ingest and recovery pipelines

#### Deliverables
- Thin compatibility wrapper in `analysis_module.py`
- Shared dataclasses and constants moved into `contracts.py`
- CLI parser and command routing moved into `cli.py`
- Artifact helpers moved into `artifacts.py`
- Import paths stable enough for downstream lanes

#### Acceptance Checks
- `python -m app.data.pipelines.daily.nba.analysis_module -h`
- `python -m app.data.pipelines.daily.nba.analysis_module build_analysis_report --season 2025-26 --season-phase regular_season`
- Existing analysis pytest still passes or is updated without behavior regression

#### Subagent Brief
- Split the NBA analysis module into an internal package without changing behavior.
- Your write scope is only the CLI, shared contracts, and artifact helpers.
- Do not redesign mart math or report logic.

### `A1` Research Universe And QA Gate
#### Objective
- Own the canonical research universe and all inclusion or exclusion rules so every other lane reads one consistent corpus.

#### Owned Files
- `app/data/pipelines/daily/nba/analysis/universe.py`
- `tests/app/data/pipelines/daily/nba/test_analysis_universe_pytest.py`

#### Read-Only Dependencies
- `app/data/databases/migrations/0018_v1_0_1__nba_analysis_mart.sql`
- `app/docs/nba_analysis_data_products.md`

#### Deliverables
- `load_analysis_universe(request)` function
- Canonical game classification:
  - `research_ready`
  - `descriptive_only`
  - excluded with reason
- Completeness report with team, date-window, and coverage breakdowns
- Deterministic filters for:
  - season
  - season phase
  - finished games only
  - coverage filter

#### Acceptance Checks
- Universe counts reconcile with current regular-season totals
- `research_ready` implies aligned PBP and in-game market path
- Missing-link and no-history games remain visible in QA output

#### Subagent Brief
- Build the research-universe loader and QA summary for the NBA analysis lane.
- Own inclusion logic, coverage classes, and completeness outputs.
- Do not write backtests, reports, or models.

### `A2` Game And Season Mart Profiles
#### Objective
- Own the team-side game profile table and season aggregate tables that answer the descriptive expectation and volatility questions.

#### Owned Files
- `app/data/pipelines/daily/nba/analysis/mart_game_profiles.py`
- `app/data/pipelines/daily/nba/analysis/mart_aggregates.py`
- `tests/app/data/pipelines/daily/nba/test_analysis_mart_game_profiles_pytest.py`

#### Owned Tables
- `nba.nba_analysis_game_team_profiles`
- `nba.nba_analysis_team_season_profiles`
- `nba.nba_analysis_opening_band_profiles`

#### Deliverables
- Extract per-team game profile rows from linked game, market, and feature inputs
- Compute:
  - opening and closing price
  - pregame and ingame ranges
  - total swing
  - MFE and MAE
  - inversion count
  - time above and below 50c
  - winner-stability timestamps
- Aggregate team-season and opening-band profiles
- Preserve `research_ready_flag` semantics from `A1`

#### Acceptance Checks
- One row per `game_id x team_side x analysis_version`
- Opening-band assignment deterministic and stable
- Aggregates recompute idempotently for same version

#### Subagent Brief
- Own team-level and season-level mart outputs for the analysis module.
- Answer team volatility, opening-band, and expectation-drift questions.
- Do not touch state-panel forward labels or strategy execution logic.

### `A3` State Panel And Context Mart
#### Objective
- Own the event-time panel that powers backtests, context studies, and future player-impact work.

#### Owned Files
- `app/data/pipelines/daily/nba/analysis/mart_state_panel.py`
- `tests/app/data/pipelines/daily/nba/test_analysis_state_panel_pytest.py`

#### Owned Tables
- `nba.nba_analysis_state_panel`
- `nba.nba_analysis_winner_definition_profiles`

#### Deliverables
- Build one aligned state row per `game_id x team_side x state_index`
- Compute:
  - event-time score context
  - clock and period context
  - favorite and lead-state flags
  - scoreboard-control mismatch flags
  - run and recent-points context
  - forward labels for swings, 50c crossing, and winner-definition stability
- Build threshold and context winner-definition profiles

#### Acceptance Checks
- State ordering deterministic within each game-side
- Forward labels use no lookahead beyond the declared horizon
- Winner-definition profile counts reconcile with raw state-panel flags

#### Subagent Brief
- Own the event-time state panel and the winner-definition aggregate surface.
- This is the main backtest and modeling substrate.
- Do not edit report formatting or strategy policies.

### `A4` Descriptive Report Pack
#### Objective
- Turn mart outputs into stable research artifacts that answer the first season-level questions directly.

#### Owned Files
- `app/data/pipelines/daily/nba/analysis/reports.py`
- `tests/app/data/pipelines/daily/nba/test_analysis_reports_pytest.py`

#### Inputs
- `A1`
- `A2`
- `A3`

#### Deliverables
- Markdown, JSON, and CSV outputs for:
  - teams most against opening expectation
  - highest average intragame volatility
  - opening-band swing profiles
  - inversion leaders
  - favorite drawdown windows
  - scoreboard-control mismatch leaders
  - quarter and score contexts with highest reversion probability
  - winner-definition stability profiles
- Stable report section ordering and artifact names

#### Acceptance Checks
- Report consumes mart outputs only
- Rebuild with same `analysis_version` produces stable section keys
- Output is readable without dashboard context

#### Subagent Brief
- Build the offline research pack from mart tables only.
- Focus on clear sectioned outputs, not UI.
- Do not query raw ingest tables directly.

### `A5` Backtest Engine
#### Objective
- Build the reusable backtest engine and first three strategy families on top of the state panel.

#### Owned Files
- `app/data/pipelines/daily/nba/analysis/backtests/engine.py`
- `app/data/pipelines/daily/nba/analysis/backtests/reversion.py`
- `app/data/pipelines/daily/nba/analysis/backtests/inversion.py`
- `app/data/pipelines/daily/nba/analysis/backtests/winner_definition.py`
- `tests/app/data/pipelines/daily/nba/test_analysis_backtests_pytest.py`

#### Inputs
- `A1`
- `A3`

#### Deliverables
- Generic trade loop over state-panel rows
- Baseline strategies:
  - favorite drawdown reversion
  - first 50c upward inversion
  - winner-definition continuation
- Per-trade outputs:
  - entry and exit timestamps
  - entry and exit price
  - gross return
  - slippage-adjusted return
  - MFE and MAE after entry
  - hold time
  - context tags

#### Acceptance Checks
- No lookahead leakage
- Slippage worsens or preserves returns monotonically
- Strategy reruns are stable for same version and same rules

#### Subagent Brief
- Implement the offline strategy engine and the three baseline strategy families.
- Consume state-panel rows only.
- Do not change mart definitions or descriptive report wording.

### `A6` Predictive Baselines
#### Objective
- Build interpretable offline baselines for the three predictive target families without introducing deep-model complexity.

#### Owned Files
- `app/data/pipelines/daily/nba/analysis/models/features.py`
- `app/data/pipelines/daily/nba/analysis/models/volatility.py`
- `app/data/pipelines/daily/nba/analysis/models/trade_quality.py`
- `app/data/pipelines/daily/nba/analysis/models/winner_timing.py`
- `tests/app/data/pipelines/daily/nba/test_analysis_models_pytest.py`

#### Inputs
- `A1`
- `A2`
- `A3`

#### Deliverables
- Shared feature extraction layer
- Baseline tracks:
  - volatility and inversion classification
  - post-trigger MFE and MAE regression
  - winner-definition timing proxy
- Time-based train and validation split
- Artifact outputs with naive baseline comparison

#### Acceptance Checks
- Time-based split only
- Metrics emitted even when data is insufficient
- Promotion blocked if baseline loses to naive comparator

#### Subagent Brief
- Build interpretable predictive baselines only.
- Use mart outputs and time-based validation.
- No LLM loop, no deep sequence model, no live API.

### `A7` Player-Impact Shadow Lane
#### Objective
- Explore the player-effect question without blocking the core v1 lane.

#### Owned Files
- `app/data/pipelines/daily/nba/analysis/player_impact.py`
- `app/docs/nba_analysis_player_impact_shadow_plan.md`
- `tests/app/data/pipelines/daily/nba/test_analysis_player_impact_pytest.py`

#### Inputs
- `A3`
- `nba.nba_player_stats_snapshots`
- saved play-by-play payloads

#### Deliverables
- Prototype player-impact research artifact for:
  - scorer presence on swing states
  - run-start and run-stop involvement
  - optional absence-driven deltas when lineups or status proxies are available
- Clear statement of what is causal, what is correlational, and what is still missing

#### Acceptance Checks
- Lane does not block mart, reports, backtests, or baselines
- Output clearly marked experimental
- No unsupported injury causality claims

#### Subagent Brief
- Research player-impact signals as a shadow lane.
- Keep the output offline and exploratory.
- Do not block or rewrite the core analysis stack.

### `A8` Consumer Adapters
#### Objective
- Expose stabilized research outputs to downstream consumers after the offline stack is trustworthy.

#### Owned Files
- adapter layer only after approval

#### Inputs
- `A4`
- `A5`
- `A6`

#### Deliverables
- Read-only adapters for dashboard or later LLM consumption
- Stable contract for summary sections, leaderboards, and recommendation context

#### Acceptance Checks
- No public write path
- Reads only versioned artifacts or mart tables
- Starts only after the offline outputs stabilize

#### Subagent Brief
- Hold this lane until the research outputs settle.
- Do not start UI or LLM-serving work early.

## Suggested Launch Order
1. Launch `A0` first and merge it before any heavy parallel code work.
2. After `A0`, launch `A1`, `A2`, and `A3` in parallel.
3. Once `A1-A3` have stable interfaces, launch `A4`, `A5`, `A6`, and `A7` in parallel.
4. Keep `A8` parked until the previous wave stabilizes.

## Suggested Branch Names
- `codex/nba-analysis-a0-contracts`
- `codex/nba-analysis-a1-universe`
- `codex/nba-analysis-a2-game-profiles`
- `codex/nba-analysis-a3-state-panel`
- `codex/nba-analysis-a4-reports`
- `codex/nba-analysis-a5-backtests`
- `codex/nba-analysis-a6-models`
- `codex/nba-analysis-a7-player-impact`

## Suggested Validation Commands
- Package split:
  - `python -m app.data.pipelines.daily.nba.analysis_module -h`
- Single-game mart smoke:
  - `python -m app.data.pipelines.daily.nba.analysis_module build_analysis_mart --season 2025-26 --season-phase regular_season --game-id 0022501197`
- Report smoke:
  - `python -m app.data.pipelines.daily.nba.analysis_module build_analysis_report --season 2025-26 --season-phase regular_season`
- Backtest smoke:
  - `python -m app.data.pipelines.daily.nba.analysis_module run_analysis_backtests --season 2025-26 --season-phase regular_season --strategy-family reversion`
- Model smoke:
  - `python -m app.data.pipelines.daily.nba.analysis_module train_analysis_baselines --season 2025-26 --season-phase regular_season --target-family volatility`
- DB pytest:
  - `$env:JANUS_RUN_DB_TESTS='1'; python -m pytest -q tests/app/data/pipelines/daily/nba`

## Ready-To-Assign Ticket List
- [ ] `A0` Split analysis package and freeze internal contracts
- [ ] `A1` Build research-universe loader and QA report
- [ ] `A2` Isolate game-team and season aggregate mart builders
- [ ] `A3` Isolate state-panel and winner-definition builders
- [ ] `A4` Build stable descriptive report pack from mart outputs only
- [ ] `A5` Implement reusable backtest engine and three baseline families
- [ ] `A6` Implement interpretable predictive baselines with time-based validation
- [ ] `A7` Build experimental player-impact shadow artifact
- [ ] `A8` Hold consumer adapters until offline outputs stabilize

## Artifact Sync Requirements
- Keep this file aligned with:
  - `app/docs/nba_analysis_module_plan.md`
  - `app/docs/nba_analysis_data_products.md`
  - `app/docs/nba_analysis_modeling_and_backtesting.md`
  - `app/docs/development_guide.md`
