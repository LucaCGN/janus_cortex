# Current Analysis System State

## Snapshot Date
- `2026-04-24`

## Locked Controllers
- primary live candidate: `controller_vnext_unified_v1 :: balanced`
- deterministic fallback: `controller_vnext_deterministic_v1 :: tight`
- controller tuning reference: [controller_vnext_final_tuning.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/reference/controller_vnext_final_tuning.md)

Interpretation:
- the controller stack is now locked for the NBA playoff live-execution phase
- controller discovery is no longer the active priority
- the active engineering lane is the local live playoff validation loop, then decision logging and executor hardening

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
- finished games validated: `22`
- split:
  - `play_in=6`
  - `playoffs=16`
- research-ready games: `22 / 22`
- state-panel rows:
  - `play_in=7,128`
  - `playoffs=18,285`
  - combined=`25,413`

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
- `/analysis-studio` now serves as the unified benchmark-control dashboard
- the dashboard is tuned around:
  - locked baseline controllers
  - replay realism gap
  - deterministic and higher-frequency challengers
  - future ML and LLM lane submissions once they publish the shared contract
- the old route and family-detail APIs remain available, but the default page is no longer a broad finalist-only strategy-lab surface
- a separate operator-first control surface now exists at `/live-control`
- `/live-control` is intentionally minimal:
  - current run status and heartbeat
  - three game cards
  - open positions and open orders
  - recent executor events and slippage summary
  - pause/resume/stop controls

## Unified Benchmark Integration Status
### Shared contract state
- replay-engine lane published the first shared replay contract on `2026-04-24`
- shared contract path:
  - `C:\code-personal\janus-local\janus_cortex\shared\benchmark_contract\replay_contract_current.md`
- current maturity:
  - `execution_replay_v1_2`
- unified comparison contract layered above it:
  - [unified_benchmark_contract.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/reference/unified_benchmark_contract.md)
- exact compare-ready gate:
  - [benchmark_compare_ready_criteria.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/reference/benchmark_compare_ready_criteria.md)
- result-mode split:
  - `standard_backtest`
  - `replay_result`
  - `live_observed`
- dominant replay divergence cause:
  - `signal_stale`

### Published compare-ready set
- locked baselines:
  - `controller_vnext_unified_v1 :: balanced`
  - `controller_vnext_deterministic_v1 :: tight`
- current promoted live-ready stack:
  - `controller_vnext_unified_v1 :: balanced`
  - `controller_vnext_deterministic_v1 :: tight`
- replay and deterministic/HF publication:
  - compare-ready now flows through the shared replay artifact package and the replay lane's own live-probe ranking
  - current replay compare-ready families are `inversion`, `quarter_open_reprice`, `micro_momentum_continuation`, `lead_fragility`, and `winner_definition`
  - current replay live-probe tier is `quarter_open_reprice` plus `micro_momentum_continuation`
  - current replay shadow set is `inversion` plus `lead_fragility`
  - current replay bench-only families still visible on the dashboard include `winner_definition`, `halftime_gap_fill`, `panic_fade_fast`, `q4_clutch`, and `underdog_liftoff`
- strongest currently published replay challenger:
  - `inversion`
- clearest new higher-frequency challenger with positive replay evidence:
  - `quarter_open_reprice`
- ML lane:
  - compare-ready shared submissions are visible on the unified dashboard
  - current role remains sidecar ranking and calibration only; skip/gate and sizing stay outside hard routing
  - current promotion bucket remains `shadow_only`
- LLM lane:
  - compare-ready constrained submissions are now visible on the unified dashboard
  - strongest current shared candidate is `llm_template_compiler_core_windows_v2`
  - current deployment recommendation remains `shadow_only`

### Shared export surfaces
- dashboard route:
  - `GET /v1/analysis/studio/benchmark-dashboard`
- export command:
  - `python tools/export_benchmark_dashboard.py`
- shared integration artifact root:
  - `C:\code-personal\janus-local\janus_cortex\shared\artifacts\benchmark-integration\`
- shared compare-ready criteria export:
  - `C:\code-personal\janus-local\janus_cortex\shared\reports\benchmark-integration\compare_ready_criteria.md`
- shared promoted-stack note:
  - `C:\code-personal\janus-local\janus_cortex\shared\reports\benchmark-integration\current_promoted_stack.md`
- shared merge-plan memo:
  - `C:\code-personal\janus-local\janus_cortex\shared\reports\benchmark-integration\milestone_merge_plan.md`
- shared submission example exports:
  - `C:\code-personal\janus-local\janus_cortex\shared\reports\benchmark-integration\ml_benchmark_submission_example.json`
  - `C:\code-personal\janus-local\janus_cortex\shared\reports\benchmark-integration\llm_benchmark_submission_example.json`

## Current Live Execution Surface
### Local live executor v1
- location:
  - `app/modules/nba/execution/*`
- execution profile:
  - `v1`
- controller core:
  - primary `controller_vnext_unified_v1 :: balanced`
  - fallback `controller_vnext_deterministic_v1 :: tight`
- current local policy:
  - run on an explicit list of NBA `game_id`s
  - fixed Polymarket minimum size only
  - limit-only entries at best ask
  - skip entry if spread is greater than `2c`
  - local stop-loss trigger with immediate market-emulated sell
  - non-stop exits try limit first, then one aggressive retry
- local orchestration ledger:
  - `C:\code-personal\janus-local\janus_cortex\tracks\live-controller\<date>\<run_id>\`
  - files:
    - `run_config.json`
    - `heartbeat.json`
    - `decisions.jsonl`
    - `executor_events.jsonl`
    - `recovery_snapshot.json`

### Live validation note for April 23, 2026
- target slate:
  - `0042500123` `NYK@ATL`
  - `0042500133` `CLE@TOR`
  - `0042500163` `DEN@MIN`
- local launcher:
  - `python tools/start_live_run.py --api-root http://127.0.0.1:8010 --run-id live-2026-04-23-v1 --game-id 0042500123 --game-id 0042500133 --game-id 0042500163 --dry-run`
- operator page:
  - `/live-control?runId=live-2026-04-23-v1`
- runbook:
  - [live_playoff_validation_runbook.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/reference/live_playoff_validation_runbook.md)

## Current Next Lanes
- immediate:
  - validate the local live executor tonight on the locked playoff slate
- after the live loop stabilizes:
  - append-only decision logging and ML-ready candidate dataset contract
  - fill-quality and slippage review
  - executor hardening around restart/recovery and stop handling

## Output Root Convention
- repo outputs remain read-only snapshots
- branch-independent artifacts and quicklook material belong under `C:\code-personal\janus-local\janus_cortex`
