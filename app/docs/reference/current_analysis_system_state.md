# Current Analysis System State

## Snapshot Date
- `2026-04-23`

## Locked Controllers
- primary live candidate: `controller_vnext_unified_v1 :: balanced`
- deterministic fallback: `controller_vnext_deterministic_v1 :: tight`
- controller tuning reference: [controller_vnext_final_tuning.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/reference/controller_vnext_final_tuning.md)

Interpretation:
- the controller stack is now locked for the NBA playoff live-execution phase
- controller discovery is no longer the active priority
- the next engineering lane is executor wiring, decision logging, and paper/live validation

## Current Release Baseline
- analysis module baseline: `v1_0_1`
- status: regular-season corpus validated, postseason corpus validated, controller-vNext tuning completed, full-season lock check completed

Completed implementation wave:
- `A0` contracts and package split
- `A1` research universe and QA gate
- `A2` game-team and season mart profiles
- `A3` state panel and winner-definition profiles
- `A4` descriptive report pack
- `A5` reusable backtest engine
- `A6` interpretable predictive baselines
- `A7` player-impact shadow lane

Completed release wave:
- `v1.0.2` safe DB validation workflow
- `v1.0.3` non-live validation workflow
- `v1.0.4` strategy-lab expansion
- `v1.1.0` benchmarked multi-algorithm backtest workflow
- `v1.2.0` stable read-only consumer adapters
- `v1.3.0` analysis studio alpha
- `v1.4.0` sequential portfolio benchmark
- `v1.4.1` repeated-seed robustness and combined keep-family sleeve
- `v1.4.2` refined underdog continuation and first routing lane
- `v1.4.3` realistic execution replay and quarter-specific sleeves
- `v1.4.4` master-router baseline and expanded family research
- `v1.4.5` restrained LLM router benchmark and finalist dashboard
- `v1.4.6` postseason event coverage, exact game-event linking, and adverse slippage contract
- `v1.4.7` controller-vNext playoff tuning, uncertainty-band LLM review, stop overlays, and family-aware sizing
- `v1.4.8` full regular-season lock check for the locked controller pair

## Corpus Snapshot
### Regular Season
- season: `2025-26`
- phase: `regular_season`
- finished games: `1224`
- research-ready games: `1198`
- descriptive-only games: `26`

### Postseason Validation Slice
- phases: `play_in`, `playoffs`
- finished games validated: `20`
- split:
  - `play_in=6`
  - `playoffs=14`
- research-ready games: `20 / 20`
- state-panel rows:
  - `play_in=7,128`
  - `playoffs=15,990`
  - combined=`23,118`

## Frozen Underlying Strategy Stack
These are the kept underlying methods that still compose the locked controllers.

### Core Families
- `winner_definition`
  - continuation / winner-likely core
- `inversion`
  - underdog reclaim / reclassification core
- `underdog_liftoff`
  - underdog continuation / rebound core

### Independent Sleeves
- `q1_repricing`
  - first-quarter repricing continuation
- `q4_clutch`
  - late close-game continuation after repeated lead changes

## Locked Controller Contract
- initial bankroll: `$10.00`
- base position fraction floor: `20%`
- target exposure fraction: `80%`
- max concurrent positions: `5`
- concurrency mode: `shared_cash_equal_split`
- sizing mode: `dynamic_concurrent_games`
- minimum order: `$1.00`
- minimum shares: `5`
- deterministic slippage: `0c`
- random adverse slippage: `0-5c`
- primary stop overlay:
  - `winner_definition` `5c` below entry

## Locked Controller Read
### Postseason Reference
On the fixed `20`-game postseason slice across slippage seeds `20260422` through `20260427`:

- `controller_vnext_unified_v1 :: balanced`
  - mean ending bankroll: `$13.84`
  - median ending bankroll: `$13.81`
  - mean max drawdown: `22.38%` / `$2.24`
  - mean minimum bankroll: `$7.76`

- `controller_vnext_deterministic_v1 :: tight`
  - mean ending bankroll: `$14.35`
  - median ending bankroll: `$14.33`
  - mean max drawdown: `29.39%` / `$4.43`
  - mean minimum bankroll: `$7.92`

Interpretation:
- the primary controller is the best postseason tradeoff candidate
- the deterministic fallback is slightly stronger on raw postseason ending bankroll, but rougher than the primary controller

### Full Regular-Season Lock Check
On the full all-games regular-season replay over the `1198` research-ready games, across the same `6` slippage seeds:

- `controller_vnext_unified_v1 :: balanced`
  - median ending bankroll: `$469,835.30`
  - mean ending bankroll: `$463,984.42`
  - range: `$382,670.48` to `$542,845.91`
  - mean max drawdown: `70.41%` / `$225,232.21`
  - mean minimum bankroll: `$4.08`
  - entered about `925` games

- `controller_vnext_deterministic_v1 :: tight`
  - median ending bankroll: `$68,486.79`
  - mean ending bankroll: `$70,940.38`
  - range: `$63,066.40` to `$85,241.79`
  - mean max drawdown: `71.09%` / `$29,438.87`
  - mean minimum bankroll: `$3.59`
  - entered about `1193` games

Interpretation:
- postseason tuning did not break the regular-season controller
- the primary controller remains strongly profitable on the full regular-season corpus
- the primary controller trades less often and remains materially less explosive than the older unified finalist, which is consistent with the intended lock behavior

## Active Validation Commands
Validated on `2026-04-23`:
- `python -m pytest -q tests/app/data/pipelines/daily/nba/test_analysis_backtests_pytest.py`
- `python tools/controller_vnext_analysis.py --season 2025-26 --analysis-version v1_0_1 --llm-model gpt-5.4 --llm-budget-usd 10.0`
- full regular-season lock check replay written under:
  - `C:\code-personal\janus-local\janus_cortex\archives\output\nba_analysis_controller_vnext\2025-26\controller_vnext_regular_full_season_all_games`

## Current Frontend Surface
- the studio remains read-only
- it should now be tuned around the locked primary controller, the deterministic fallback, and their internal route / sleeve diagnostics

## Current Next Branches
- `codex/live-polymarket-executor`
  - primary branch to wire the locked controller into paper / live-safe execution
- `codex/controller-decision-logging`
  - append-only decision log, executor outcomes, and ML-ready candidate dataset
- `codex/frontend-analysis-portfolio-viz`
  - focused review dashboard for the locked controller pair

## Output Root Convention
- repo outputs remain read-only snapshots
- branch-independent artifacts and quicklook material belong under `C:\code-personal\janus-local\janus_cortex`
