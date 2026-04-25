# NBA Analysis Next Steps

## End Goal
Turn the locked NBA controller into the first live-safe Polymarket execution module with:
- one primary controller
- one no-LLM fallback
- append-only decision and execution logs
- a read-only review surface for route / sleeve diagnostics

Detailed milestone and branch decomposition lives in:
- [roadmap_to_multi_algo_backtests.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/roadmap_to_multi_algo_backtests.md)
- [benchmark_integration_roadmap.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/benchmark_integration_roadmap.md)
- [branches/README.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/README.md)

## What Is Already Done
- `A0-A7` implementation wave completed
- controller-vNext playoff tuning completed
- locked primary controller:
  - `controller_vnext_unified_v1 :: balanced`
- locked deterministic fallback:
  - `controller_vnext_deterministic_v1 :: tight`
- full regular-season all-games lock check completed
- fixed postseason validation corpus completed:
  - `6` play-in games
  - `16` playoff games
- Polymarket event history linked for the full postseason corpus
- play-by-play loaded for the full postseason corpus

## Frozen Current State

### Locked Controllers
- primary:
  - `controller_vnext_unified_v1 :: balanced`
- fallback:
  - `controller_vnext_deterministic_v1 :: tight`

### Underlying Family Set
- routed core:
  - `winner_definition`
  - `inversion`
  - `underdog_liftoff`
- independent sleeves:
  - `q1_repricing`
  - `q4_clutch`

### Locked Replay Contract
- start bankroll: `$10.00`
- base position floor: `20%`
- target exposure: `80%`
- max concurrent positions: `5`
- sizing mode: `dynamic_concurrent_games`
- min order: `$1.00`
- min shares: `5`
- random adverse slippage: `0-5c`
- primary stop overlay:
  - `winner_definition` `5c` below entry

### Current Validation Read
- postseason reference:
  - primary controller mean ending bankroll: `$13.84`
  - primary controller mean max drawdown: `22.38%` / `$2.24`
  - fallback mean ending bankroll: `$14.35`
  - fallback mean max drawdown: `29.39%` / `$4.43`
- full regular season:
  - primary controller median ending bankroll: `$469,835.30`
  - fallback median ending bankroll: `$68,486.79`

Interpretation:
- the primary controller is locked because it is the best playoff tradeoff
- the fallback remains available if no LLM should be used live
- strategy discovery is no longer the main task

## Immediate Critical Path

### 1. Local Live Playoff Validation Loop

Why this is next:
- the controller is now locked
- the highest-value missing piece is execution wiring, not more backtest exploration
- we need to validate the locked controller on live playoff conditions before broader app/deployment work

Target outputs:
- local live executor v1 around the locked controller pair
- run-until-finished control loop over an explicit list of `game_id`s
- bounded order placement, cancel, replace, and local stop primitives
- separate `/live-control` surface for operator monitoring
- restart/resume safety through local ledger + DB parity

Today’s live scope:
- games:
  - `0042500123` `NYK@ATL`
  - `0042500133` `CLE@TOR`
  - `0042500163` `DEN@MIN`
- execution profile:
  - `v1`
- primary controller:
  - `controller_vnext_unified_v1 :: balanced`
- fallback:
  - `controller_vnext_deterministic_v1 :: tight`
- live sizing:
  - fixed Polymarket minimum only
- order policy:
  - limit entries at best ask
  - stop-loss exits as local trigger + market-emulated sell
- launcher:
  - `python tools/start_live_run.py --api-root http://127.0.0.1:8010 --run-id live-2026-04-23-v1 --game-id 0042500123 --game-id 0042500133 --game-id 0042500163 --dry-run`

### 2. Decision Logging And ML-Ready Dataset
Why this is next:
- once the controller is locked, every live or paper decision should produce training-grade records

Target outputs:
- append-only candidate-decision log
- selected family / confidence / sleeve / stop-overlay / final stake fields
- execution outcome log:
  - requested price
  - filled price
  - cancel / miss / partial-fill state
- training-ready candidate table for later ML ranking and sizing work

### 3. Focused Review Dashboard
Why this is parallel-friendly:
- the controller set is now narrow and stable

Target outputs:
- primary vs fallback review surface
- route mix and sleeve trigger diagnostics
- live paper-review queue
- outcome and bankroll-path inspection

### 3b. Unified Benchmark Control
Why this is parallel-friendly:
- replay, ML, and LLM lanes need one comparison layer before they can merge cleanly

Target outputs:
- one shared benchmark contract above lane-specific artifacts
- one dashboard that shows baselines, replay realism gap, deterministic and HF candidates, ML candidates, and LLM candidates
- explicit standard backtest vs replay result vs live observed views
- explicit stale-signal suppression metrics so replay realism does not drift back into vague trade-count summaries
- one merge gate for when a lane is mature enough to compare globally
- one shared export snapshot under the Codex coordination space

Current read:
- replay is the realism baseline
- `signal_stale` is the dominant divergence cause
- `quarter_open_reprice` is the clearest promising replay-aware HF family

### 4. Season Continuity
Target outputs:
- fresh-game sync and rebuild playbook for the remaining playoffs
- season handoff path toward preseason and WNBA bootstrap

## What We Are Not Prioritizing Right Now
- new strategy-family proliferation
- broad LLM prompt experimentation
- free-form LLM autonomy
- replacing the controller with ML before live execution data exists
- broad studio or operator pages that do not help review the locked controller pair
- lane-specific scorecards that fork the benchmark semantics

## Execution Notes
- work is now intentionally concentrated on `main` for local iteration speed
- strategy math stays frozen unless a real live-execution issue proves otherwise
- execution changes should version the execution profile (`v1`, `v2`, `v3`) instead of renaming the controller core
- tonight's acceptance gate is operational:
  - live runner stays alive
  - orders/events appear on `/live-control`
  - restart/resume does not duplicate entries
  - fill/slippage data is captured for every attempted trade
