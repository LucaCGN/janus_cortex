# Janus Core Live Trading Runtime

Status: draft source-of-truth contract
Created: 2026-05-24
GitHub issue: https://github.com/LucaCGN/janus_cortex/issues/63

## Purpose

Define the target Janus covered-market live trading runtime after the 2026-05-20 NBA/WNBA live-window review.

This is not a new strategy lane and not a Codex portfolio-manager feature. It is the app-owned Janus runtime boundary for NBA/WNBA covered markets and future live domains. Codex, internal LLMs, and the operator can contribute research, overrides, and configuration, but Janus must retain an internal deterministic/ML trading engine that can continue when pregame Codex automation or LLM calls fail.

## Binding Principles

1. Pregame Codex research is an optional prior, never a liveness dependency.
2. Strategy lanes become signal producers. They do not own order placement.
3. Janus live runtime aggregates signals, applies event config and risk gates, then emits StrategyPlan revisions or order intents through existing Janus gates.
4. Deterministic and ML signals may continue in degraded mode when Codex or internal LLM is unavailable, provided feed, CLOB, kill-switch, risk, worker, and order-path gates are green.
5. NBA and WNBA share the same normalized live snapshot, signal, aggregation, and execution interfaces. Feed adapters remain league-specific until source parity is proven.
6. Live learning uses controlled live windows. Shadow/replay is still required for promotion, but shadow-only work must not consume the live window when approved live tests can safely run.
7. Risk budget is derived from portfolio/cash percentage caps plus absolute caps, not a hard-coded nominal value.

## Actors

| Actor | Runtime role | Binding limit |
|---|---|---|
| Janus FastAPI trading system | Owns data sync, live feeds, signal aggregation, StrategyPlan evaluation, order gating, live worker, and ledgers. | Must not depend on Codex chat memory or pregame automation for liveness. |
| Internal Janus LLM | Adds strategy review, activation/deactivation proposals, and narrative reasoning when available. | Output must become reviewed StrategyPlan/config/signal artifacts before execution. |
| Codex integrations | Dev loop, oversight, Obsidian, portfolio-manager, pregame research, postgame planning, issue work. | Codex may update docs/config/issues and propose changes; it is not the live covered-market executor. |
| Human operator | Owns project direction, manual trading actions, strategy insights, and explicit approval boundaries. | Manual actions must be reconciled into Janus evidence before becoming system truth. |

## Runtime Components

| Component | Responsibility | First implementation route |
|---|---|---|
| League data sync adapters | Scheduled data refresh for NBA, WNBA, and future leagues. | Split sync ownership by league; do not make one monolithic daily service the only source. |
| Live data streaming adapters | Scoreboard, play-by-play, stats, CLOB/orderbook, and account inventory feeds with freshness and latency metadata. | NBA/WNBA adapters normalize to the same snapshot contract. |
| Pre-event research agents | Produce priors, player/team context, likely regimes, and candidate signal configs. | Optional input to runtime; missing output must not block live. |
| Signal producers | Deterministic, ML, scoreboard, play-by-play, band/grid, LLM, Codex, and operator/user signals. | See `automation/live_signal_aggregation_contract.md`. |
| Signal aggregation system | Merges signals, resolves conflicts, applies event config/risk, and chooses monitor/hold/buy/sell/rebuy/reduce outputs. | New app module and tests under `#63` child slices. |
| StrategyPlan bridge | Converts aggregated decisions into StrategyPlan revisions and order intents. | Existing StrategyPlan evaluator remains the execution gate. |
| Execution and sleeve manager | Maintains per-event sleeves: core hold, grid/scalp, rebuy, reduce/stop, and settlement. | Minimum parallel test structure is 5-share grid plus 5-share core when risk gates allow. Grid/scalp sleeves require paired microcycle state, not repeated independent buys. |
| Live game context layer | Classifies the current game scenario, reports sleeve-level ML/PBP confidence, computes dynamic realized-profit risk, and emits narrowly bounded opportunistic candidates when current sleeves miss a valid entry/exit point. | `app/modules/agentic/live_game_context.py`, wired into the live tick and aggregation artifact on 2026-05-27. |
| Reduce/stop lifecycle layer | Converts StrategyPlan stop metadata, direct inventory, target state, CLOB exit price, and game phase into active reduce/exit evidence. | `app/modules/agentic/reduce_stop_lifecycle.py`, wired into the live tick and aggregation artifact on 2026-05-28 under #82. |
| Postgame performance review | Reviews fired/missed/blocked signals, fills, orderbook, latency, PnL, and strategy confidence. | Must feed issue closure, strategy config, and Obsidian lessons. |

## Live Snapshot Contract

The runtime must normalize league and CLOB state into a single event snapshot before signal aggregation.

Required fields:

| Field group | Required data |
|---|---|
| Event identity | league, event id, market slug, team/outcome mapping, start time, status |
| Game state | score, period, clock, possession when available, player status when available |
| Feed state | source, timestamp, latency, stale flag, confidence |
| CLOB state | token id, bid/ask/mid, spread, top depth, minimum size, tick size, book timestamp |
| Account state | event-scoped positions, open orders, fills, inventory cost, realized/unrealized result |
| Runtime state | active StrategyPlan version, worker heartbeat, kill-switch, live-enabled flags, current sleeves |
| Evidence state | artifact paths, source URLs/API responses, blocker reason codes |

Scoreboard and play-by-play are separate feeds. They must not be collapsed before freshness and conflict handling because they can have different latency and failure modes.

## Risk Budget Model

Do not encode `$10` as a literal strategy rule.

The event cap is derived:

```text
event_cap_usd = min(
  portfolio_value_usd * event_cap_pct,
  available_cash_usd * cash_cap_pct,
  absolute_event_cap_usd
)
```

Initial live-learning defaults at the current account scale:

| Parameter | Initial value | Reason |
|---|---:|---|
| `event_cap_pct` | `0.10` | Allows roughly 10% of portfolio value per live game while capital is intentionally experimental. |
| `cash_cap_pct` | `0.20` | Prevents one event from consuming too much available cash. |
| `absolute_event_cap_usd` | `10.00` | Current practical ceiling for one game until repeated evidence improves. |
| `max_concurrent_events` | `5` | Allows several live windows without a single game starving all others. |
| `max_grid_leg_shares` | `5` | Matches Polymarket minimum-order practical testing. |
| `minimum_parallel_sleeve` | `10 shares when gates allow` | Enables 5-share grid/scalp plus 5-share core hold. |

If cash or portfolio value changes, the cap changes mechanically. The absolute cap can be raised only by issue-backed risk calibration and operator approval.

## Degraded Mode Requirements

| Failure | Required behavior |
|---|---|
| Pregame Codex automation missing or paused | Janus uses default event config and any stored priors; deterministic/ML signals continue if live gates are green. |
| Internal LLM unavailable or budget-blocked | LLM signal producer is disabled; deterministic/ML/Codex/user signals remain eligible. |
| Codex unavailable | App-owned runtime continues; no docs/issues/Obsidian updates until Codex returns. |
| WNBA adapter incomplete | WNBA fails closed for execution but still captures evidence; NBA is not blocked. |
| Scoreboard stale but CLOB live | No new order unless event config explicitly permits CLOB-only mode and current inventory/risk gates pass. |
| CLOB unavailable | Live execution fails closed even if scoreboard or LLM signals are strong. |

### Implemented Fallback Slice

As of 2026-05-24, `codex_tool/run_live_strategy_tick.py` implements the first `#68` degraded-mode slice: when `current_strategy_plan` is missing and the caller passes `--submit-candidate-strategy-plan`, the tick stores a system-owned monitor-only fallback StrategyPlan and continues dry live evidence collection instead of stopping at `missing_current_strategy_plan`.

The fallback is intentionally non-executable: `shadow_only=true`, `entry_disabled=true`, `must_not_place_orders=true`, `budget_usd=0`, and `max_positions=0`. It removes the liveness dependency on pregame Codex automation, but live orders still require an event-specific executable StrategyPlan with direct market/outcome/token mapping, event-scoped inventory proof, risk budget, target/stop/rebuy rules, and normal Janus execute/live-worker gates.

### Implemented Executable Plan Bootstrap Slice

As of 2026-05-24, `codex_tool/build_live_strategy_plan.py` implements the first `#63/#67` executable-plan bridge. It imports a Polymarket NBA/WNBA event URL into Janus catalog truth, maps the moneyline market/outcomes to catalog UUIDs and token ids, builds a validated StrategyPlanJSON, and can submit that plan as the current event plan.

The bootstrapper does not place orders. Execution remains behind `evaluate_strategy_plan`, live-worker, direct CLOB/account truth, kill-switch, runtime activation, and order-management gates.

Supported live-test plan shapes:

| Mode | Use | Sleeve behavior |
|---|---|---|
| `selected_outcome` | Operator/Codex has selected the team/side to test. | Splits `10` shares into 5-share grid/scalp plus 5-share core hold when gates allow. |
| `responsive_both_sides` | No side is selected but both moneyline sides should be available to signals. | Creates one 5-share grid/scalp sleeve per outcome, capped by operator notional gates. |

The live tick and worker now accept `max_buy_notional_usd`, and the evaluator can respect StrategyPlan `size_policy=plan_size` while still enforcing minimum size, minimum buy notional, and maximum buy notional. This prevents the prior failure mode where every buy collapsed to a minimum-order heuristic and made parallel sleeve testing impossible.

### Implemented Paired Microcycle Slice

As of 2026-05-25, #77 implements paired microcycle evidence and readback scoring. It does not submit orders by itself; it gives the live tick a per-sleeve cycle state that can be bound into aggregation.

The target behavior is not "buy whenever low-price signals appear." It is:

1. create or import a bounded buy leg;
2. when that buy fills, immediately create or manage the paired sell leg through Janus gates;
3. while that sell is unresolved, block duplicate same-cycle buys;
4. when the sell fills, either close the cycle or request a paired rebuy after fresh score/CLOB/event-control review;
5. allow multiple concurrent cycles only when they are distinct by band/sleeve and event controls allow parallel cycles.

This is the missing execution link between resistance-band/retest signals and actual 1c-3c interval trading.

### Implemented Trigger-To-Sleeve Binding Slice

As of 2026-05-25, `app/modules/agentic/sleeve_trigger_binding.py` connects the live tick trigger surface to the sleeve model before aggregation.

Current behavior:

1. StrategyPlan sleeve states become sleeve-scoped live signals.
2. Paired microcycle `sell_candidate` and `sell_stale_replace` rows become sleeve-scoped sell signals.
3. Paired microcycle `rebuy_candidate` rows become sleeve-scoped rebuy signals.
4. Paired microcycle `sell_open_waiting` rows emit local sleeve blockers so the same cycle does not keep buying while its paired sell is unresolved.
5. Aggregation order-intent candidates retain `sleeve_id`, `sleeve_group`, `sleeve_role`, `strategy_id`, `strategy_family`, `cycle_id`, `trigger_type`, and `trigger_source`.
6. StrategyPlan evaluate/execute can promote aggregation candidates into Janus `OrderIntent`s after deduping any normal StrategyPlan intent for the same token/side/sleeve/cycle.

This keeps score-gap, band, microcycle, and LLM/review triggers from becoming detached from the sleeve that owns them. It also prevents a single local strategy gate from suppressing unrelated sleeves when global Janus safety gates are green.

### Implemented Live Game Context Slice

As of 2026-05-27, `app/modules/agentic/live_game_context.py` is wired into `codex_tool/run_live_strategy_tick.py`.

The live tick now emits `live_game_context_evidence_v1` under both `market_state["live_game_context"]` and `live_signal_aggregation["live_game_context"]`.

The context layer answers four questions that were previously only implicit:

1. What scenario is the game in: S/A/B/C/D, with exact classifier inputs?
2. Which sleeves does the scenario support or suppress?
3. Which sleeve-level ML/PBP confidence exists today, and is it executable?
4. Does realized event/day profit unlock additional bounded risk for an entry/exit point not covered by current sleeves?

Current ML/PBP confidence remains evidence-only. It is derived from deterministic PBP annotation plus the scenario classifier, with an optional `gpt-5.4-nano` dispatcher available when live LLM dispatch and runtime credentials are enabled. Nano can tag and escalate review windows, but it cannot execute or bypass StrategyPlan/live-worker/order-management gates.

Standalone opportunistic candidates are allowed into aggregation only when:

- the scenario is not `D` or `U`;
- fresh underdog/favorite outcome prices are available;
- realized-profit risk budget funds at least the exchange minimum;
- the candidate declares a paired lifecycle policy;
- normal StrategyPlan/live-worker/order-management gates still pass.

This gives Janus a controlled path for "proper entry/exit point detected outside current sleeves" without turning the context layer into an order executor.

The aggregation event-budget readback now consumes this live context risk state. Realized event/day profit can add bounded reinvestment capacity, while unresolved loss exposure can reduce event cap before new candidates are selected.

### Implemented Reduce/Stop Lifecycle Slice

As of 2026-05-28, `app/modules/agentic/reduce_stop_lifecycle.py` is wired into `codex_tool/run_live_strategy_tick.py`.

The live tick now emits `sports_live_reduce_stop_lifecycle_evidence_v1` under both `market_state["reduce_stop_lifecycle"]` and `live_signal_aggregation["reduce_stop_lifecycle"]`.

This layer exists because the May 27 WNBA slate showed positive trading/scalping behavior but unresolved losing residuals. It answers:

1. Does this sleeve have account-confirmed inventory?
2. What is the weighted basis and current direct CLOB exit price?
3. Have StrategyPlan stop thresholds been crossed?
4. Is the game in Q4/endgame loss mode, adverse thesis failure, or final cleanup?
5. Should Janus block new rebuys and attempt a reduce/exit candidate through normal gates?

Reduce/stop lifecycle is deterministic app-owned evidence. It may emit `reduce` signals, but it never places or cancels orders directly. Final-state rows are cleanup/reconciliation rows and do not create new target orders.

### Implemented WNBA Live Adapter Slice

As of 2026-05-24, WNBA event ticks route through WNBA-specific live sync and read endpoints before evaluation. NBA still uses NBA endpoints. This removes the previous NBA-only live-state dependency for WNBA covered-market tests, while preserving fail-closed behavior if WNBA scoreboard/play-by-play, CLOB, or orderbook freshness is missing.

## Issue Routing

`#63` owns the architecture and implementation route for Janus core live trading.

Existing issues are reclassified:

| Issue | New role under `#63` |
|---|---|
| `#61` | Completed NBA OKC/SAS live execution foundation; future NBA runtime gaps route to focused #63 follow-ups and closed #55/#70 evidence instead of reopening old buckets. |
| `#62` | Active WNBA controlled minimum-size promotion evidence and blocker route. |
| `#55` | Entry timing, fillability, and event-start expiry research feeding signal confidence. |
| `#42` | Polymarket minimum-order and market-order exception support. |
| `#44` | Risk ladder and account/bankroll calibration support. |
| `#56/#59` | Global portfolio-manager only; not Janus covered-market runtime owners. |
| `#46/#47/#48` | Future-domain/profile context; not live covered-market authority. |

Implementation child issues:

| Issue | Scope |
|---|---|
| [#64](https://github.com/LucaCGN/janus_cortex/issues/64) | Closed foundation: NBA/WNBA normalized snapshot review and live-tick adoption. |
| [#65](https://github.com/LucaCGN/janus_cortex/issues/65) | Closed foundation: signal producer schema and artifact persistence. |
| [#66](https://github.com/LucaCGN/janus_cortex/issues/66) | Closed foundation: aggregator conflict/risk/cooldown arbitration and blocker artifacts. |
| [#67](https://github.com/LucaCGN/janus_cortex/issues/67) | Closed foundation: event budget and sleeve helper. |
| [#68](https://github.com/LucaCGN/janus_cortex/issues/68) | Closed foundation: deterministic fallback when pregame/LLM is missing. |
| [#69](https://github.com/LucaCGN/janus_cortex/issues/69) | Closed foundation: runtime control endpoints for signal producer activation and event config changes. |
| [#70](https://github.com/LucaCGN/janus_cortex/issues/70) | Closed foundation: postgame signal-performance review, missed-signal replay, no-bid/min-price quarantine, and project-chief readback synchronization. |
| [#82](https://github.com/LucaCGN/janus_cortex/issues/82) | Active follow-up: reduce/stop lifecycle, Q4/endgame loss mode, adverse-thesis rebuy suppression, and final cleanup. |
| [#83](https://github.com/LucaCGN/janus_cortex/issues/83) | Active follow-up: WNBA postgame evidence recovery, LLM usage analysis, blocker efficacy review, and account-trade backfill routing. |

## Acceptance Check

This contract remains open until the implemented normalized snapshot, signal, aggregation, event-budget, runtime-control, and StrategyPlan bridge foundations are adopted by the live worker as the ordinary covered-market runtime path with lot-level target management and postgame learning evidence. This file is a planning authority and issue-routing contract, not live-order permission.
