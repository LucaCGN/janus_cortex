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
| Execution and sleeve manager | Maintains per-event sleeves: core hold, grid/scalp, rebuy, reduce/stop, and settlement. | Minimum parallel test structure is 5-share grid plus 5-share core when risk gates allow. |
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

## Issue Routing

`#63` owns the architecture and implementation route for Janus core live trading.

Existing issues are reclassified:

| Issue | New role under `#63` |
|---|---|
| `#61` | NBA live execution evidence and blocker route. |
| `#62` | WNBA controlled minimum-size promotion evidence and blocker route. |
| `#55` | Entry timing, fillability, and event-start expiry research feeding signal confidence. |
| `#42` | Polymarket minimum-order and market-order exception support. |
| `#44` | Risk ladder and account/bankroll calibration support. |
| `#56/#59` | Global portfolio-manager only; not Janus covered-market runtime owners. |
| `#46/#47/#48` | Future-domain/profile context; not live covered-market authority. |

Implementation child issues:

| Issue | Scope |
|---|---|
| [#64](https://github.com/LucaCGN/janus_cortex/issues/64) | NBA/WNBA live adapter parity and normalized snapshot tests. |
| [#65](https://github.com/LucaCGN/janus_cortex/issues/65) | Signal producer schema and persistence. |
| [#66](https://github.com/LucaCGN/janus_cortex/issues/66) | Aggregator conflict/risk/cooldown arbitration and blocker artifacts. |
| [#67](https://github.com/LucaCGN/janus_cortex/issues/67) | Event budget and sleeve manager. |
| [#68](https://github.com/LucaCGN/janus_cortex/issues/68) | Deterministic fallback when pregame/LLM is missing. |
| [#69](https://github.com/LucaCGN/janus_cortex/issues/69) | Runtime control endpoints for signal producer activation and event config changes. |
| [#70](https://github.com/LucaCGN/janus_cortex/issues/70) | Postgame signal-performance review and missed-signal replay. |

## Acceptance Check

This contract is satisfied when future code implements the normalized snapshot, signal, aggregation, event-budget, and StrategyPlan bridge surfaces with tests and runtime artifacts. Until then, this file is a planning authority and issue-routing contract, not live-order permission.
