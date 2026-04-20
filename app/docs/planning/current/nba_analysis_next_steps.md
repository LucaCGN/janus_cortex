# NBA Analysis Next Steps

## End Goal
Turn the current offline NBA analysis stack into a repeatable trading-research program that can:
- measure human-expectation drift and intragame odds volatility
- compare multiple trading algorithms on the same state-panel substrate
- validate profitability against naive baselines, random holdouts, and slippage stress
- expose visual feedback and operator controls through a dedicated frontend module
- carry the program forward into playoffs, preseason, and WNBA continuity work

## What Is Already Done
- `A0-A7` implementation wave completed:
  - package split
  - research universe and QA layer
  - mart builders
  - descriptive report pack
  - backtest engine
  - predictive baselines
  - player-impact shadow lane
- current CLI surface present:
  - `build_analysis_mart`
  - `build_analysis_report`
  - `run_analysis_backtests`
  - `train_analysis_baselines`

## Validation Status As Of 2026-04-19
- analysis test sweep:
  - `15 passed`
  - `11 skipped`
- skipped checks are Postgres-gated integration validations controlled by `JANUS_RUN_DB_TESTS=1`
- CLI smoke passed:
  - `python -m app.data.pipelines.daily.nba.analysis_module -h`
- current evidence says the offline structure is in place, but the full non-live DB validation wave is still pending

## Current Gaps
- no formal disposable Postgres plus dev-clone workflow is documented or automated yet
- full-season mart, report, backtest, and model validations still need a non-live DB runbook
- strategy comparison is still at the baseline-family stage rather than a full experiment framework
- there is no permanent frontend module yet
- playoffs, preseason, and WNBA continuity work are not yet organized into dedicated branches

## Next Phase Order

### 1. Data Safety And Validation Foundation
Branches:
- `codex/data-dev-db-safety`
- `codex/ops-analysis-validation`

Goals:
- create the disposable Postgres and dev-clone validation workflow
- run full-season mart, report, backtest, and baseline validations without touching the live DB
- capture a reproducible validation checklist and results ledger

Exit criteria:
- migration and DB tests pass on disposable Postgres
- full analysis CLI smoke is run on a non-live database
- validation results are stored in the local planning ledger

### 2. Strategy Research And Experiment Framework
Branches:
- `codex/analysis-strategy-lab`
- `codex/analysis-sampling-benchmarking`

Goals:
- turn descriptive findings into comparable trading algorithms
- add experiment registry outputs, benchmark comparisons, and random-sample holdouts
- compare strategy lines against naive winner-prediction and base-rate references

Exit criteria:
- each strategy family produces comparable metrics and artifacts
- 5%-10% random holdout is wired into the validation workflow
- slippage-adjusted comparisons are available across strategies

### 3. Consumer Adapters And Frontend
Branches:
- `codex/analysis-a8-consumer-adapters`
- `codex/frontend-analysis-studio`

Goals:
- freeze read-only contracts for reports, leaderboards, and model outputs
- build the permanent frontend module for monitoring, triggering, and visualizing analysis work

Exit criteria:
- frontend reads stable adapter contracts, not raw ingest tables
- operator can inspect game context, strategy results, and baseline metrics visually

### 4. Season Continuity
Branches:
- `codex/season-playoffs-preseason`
- `codex/season-wnba-bootstrap`

Goals:
- keep the data platform alive beyond the closed regular season
- prepare playoff, preseason, and WNBA structures without breaking the regular-season analysis lane

Exit criteria:
- season-scope schemas and sync logic are in place
- offseason work can continue on basketball data rather than waiting for the next NBA regular season

## Product Questions To Keep Answering
- when is a game statistically defined, and which comeback classes still break that rule?
- which entry and exit styles are actually profitable after slippage and hold-time constraints?
- which odds paths are driven by repeatable context versus isolated narratives?
- which player-presence or play-type effects are strong enough to matter without over-claiming causality?
