# NBA Analysis Next Steps

## End Goal
Turn the locked NBA controller into the first live-safe Polymarket execution module with:
- one primary controller
- one no-LLM fallback
- append-only decision and execution logs
- a read-only review surface for route / sleeve diagnostics

Detailed milestone and branch decomposition lives in:
- [roadmap_to_multi_algo_backtests.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/roadmap_to_multi_algo_backtests.md)
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
  - `14` playoff games
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

### 1. Live Polymarket Executor
Branch:
- `codex/live-polymarket-executor`

Why this is next:
- the controller is now locked
- the highest-value missing piece is execution wiring, not more backtest exploration

Target outputs:
- paper/live-safe executor around the locked controller pair
- bounded order placement, cancel, replace, and risk-stop primitives
- one clear controller selection path:
  - primary controller by default
  - deterministic fallback available behind config / operator switch

### 2. Decision Logging And ML-Ready Dataset
Branch:
- `codex/controller-decision-logging`

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
Branch:
- `codex/frontend-analysis-portfolio-viz`

Why this is parallel-friendly:
- the controller set is now narrow and stable

Target outputs:
- primary vs fallback review surface
- route mix and sleeve trigger diagnostics
- live paper-review queue
- outcome and bankroll-path inspection

### 4. Season Continuity
Branch:
- `codex/season-playoffs-preseason`

Target outputs:
- fresh-game sync and rebuild playbook for the remaining playoffs
- season handoff path toward preseason and WNBA bootstrap

## What We Are Not Prioritizing Right Now
- new strategy-family proliferation
- broad LLM prompt experimentation
- free-form LLM autonomy
- replacing the controller with ML before live execution data exists
- broad studio/operator pages that do not help review the locked controller pair
