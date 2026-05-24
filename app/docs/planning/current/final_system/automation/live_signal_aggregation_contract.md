# Janus Live Signal Aggregation Contract

Status: draft automation/runtime contract
Created: 2026-05-24
GitHub issue: https://github.com/LucaCGN/janus_cortex/issues/63

## Purpose

Define how Janus should turn many live evidence sources into one event-scoped decision stream.

The signal aggregation system replaces the old mental model where each strategy lane tries to become a semi-independent executor. Lanes produce signals. The aggregator decides whether the event should monitor, buy, sell, rebuy, reduce, hold, or block.

## Source Classes

| Source class | Examples | Can execute alone? |
|---|---|---|
| Deterministic | Score gap, period, clock, spread, book depth, inventory, order expiry, band retest. | Yes, if approved and all runtime gates are green. |
| ML | PBP price-impact model, band/grid model, regime classifier, fillability model. | Yes, only after backtest/live-evidence promotion. |
| LLM | Internal Janus strategy review, hypothesis synthesis, trigger interpretation. | No; output must become reviewed config/signal artifacts. |
| Codex | Operator-facing analysis, config patches, issue/docs updates, fallback StrategyPlan drafting. | No direct covered-market execution. |
| Operator/user | Manual insight, approval, direct manual action, strategy override. | Manual actions require reconciliation before Janus treats them as system state. |

## Signal Schema

Every signal producer should emit a structured signal with these fields:

| Field | Meaning |
|---|---|
| `signal_id` | Stable id for replay and dedup. |
| `event_id` | Janus event id or mapped external event key. |
| `market_token_id` | CLOB token/outcome when known. |
| `source` | Deterministic, ML, LLM, Codex, operator, or feed. |
| `signal_type` | `buy`, `sell`, `rebuy`, `reduce`, `hold`, `block`, `activate`, `deactivate`, `monitor`. |
| `side` | Outcome side when relevant. |
| `price_band` | Current price, support/resistance band, or target band. |
| `confidence` | Numeric or enum confidence with calibration source. |
| `freshness` | Source timestamp, latency, stale flag. |
| `reason_codes` | Machine-readable reasons such as `q4_rebound_band`, `pbp_player_on_court`, `spread_too_wide`. |
| `risk_request` | Requested notional/shares and sleeve. |
| `falsification` | Conditions that invalidate the signal. |
| `evidence_paths` | Runtime artifacts, source snapshots, or postgame replay paths. |

Signals are evidence. They are not orders.

## Aggregation Responsibilities

The aggregator must:

1. Load the current event snapshot and event control config.
2. Deduplicate signals by source/event/price band/cooldown.
3. Reject stale or source-conflicted signals.
4. Merge confirming signals into a stronger decision.
5. Preserve conflicting signals as blockers or monitor-only decisions.
6. Apply current inventory, open orders, filled orders, realized result, and event budget.
7. Select sleeve action: core hold, grid/scalp, rebuy, reduce/stop, or monitor.
8. Emit a StrategyPlan revision or an execution blocker artifact.

## Event Control Config

Each live event should expose mutable config that Codex, internal LLM, operator, or Janus runtime can update through approved endpoints:

| Config | Purpose |
|---|---|
| `enabled_signal_sources` | Which deterministic/ML/LLM/user signal producers are active. |
| `event_cap_pct` | Portfolio percentage cap for this event. |
| `absolute_event_cap_usd` | Hard cap for this event. |
| `max_grid_leg_shares` | Per-grid-leg share cap. |
| `core_hold_shares` | Shares reserved from first entry for later game phases. |
| `buy_drop_cents` | Minimum price drop before a rebound buy can fire. |
| `sell_profit_pct` | Minimum profit target when no band target is stronger. |
| `support_band_count_min` | Number of persistent bands needed for high-volatility mode. |
| `cooldown_seconds` | Per-signal cooldown to prevent repeated same-price churn. |
| `rebuy_requires_fresh_review` | Whether rebuy needs fresh score/CLOB review after target fill. |
| `llm_allowed` | Whether internal LLM can propose signal activation/deactivation. |

## Resistance Band Volatility Component

The 2026-05-20 Spurs/Thunder postgame review promotes a component idea, not a standalone executor.

Rules to test:

1. A spike before a drop becomes a resistance band.
2. A low before a rebound becomes a support band.
3. Bands strengthen when retested across enough direct CLOB ticks or elapsed time.
4. High-volatility mode can activate when at least `3` bands persist and spread/depth/score-clock gates are valid.
5. In high-volatility mode, buy signals should target support retests after downward impulses and sell signals should target the next upper band or a bounded cent/profit target.

The component must use direct CLOB/orderbook and score-clock evidence, not delayed chart pixels.

## Execution Boundary

The aggregator may emit:

- `monitor_only`
- `blocked`
- `strategy_plan_revision`
- `order_intent_candidate`

It must not place, cancel, replace, submit, sign, broadcast, or redeem orders directly.

Execution still belongs to Janus StrategyPlan evaluate/execute/live-worker/order-management gates.

## StrategyPlan Bridge Requirements

When the live runtime needs an executable plan and no reviewed pregame plan is available, the bridge must build or adopt a current StrategyPlan instead of leaving the event monitor-only.

Minimum executable bridge contract:

- direct Polymarket catalog import/mapping for the event URL;
- catalog UUID `market_id` and `outcome_id`, plus token ids, in every order intent;
- `price_policy=current_ask` or another explicit dynamic policy, not stale screenshot/frontend prices;
- `size_policy=plan_size` only when operator sizing gates include a max notional cap;
- `max_buy_notional_usd` on the live tick/worker path for per-event budget control;
- target, stop, rebuy/revision, feed freshness, spread, clock, and score-gap rules;
- 5-share grid/scalp sleeve and, when allowed by budget, separate 5-share core hold sleeve;
- no direct execution by the bridge itself.

The current bootstrap implementation is `codex_tool/build_live_strategy_plan.py`. It supports:

- `selected_outcome`: one selected side split into grid/core sleeves when `total_shares >= 10`;
- `responsive_both_sides`: one grid sleeve for each moneyline side when no selected side exists.

Live runtime operators should use this bridge to replace monitor-only fallback plans before a live window if the normal pregame planner is missing, paused, or stale.

## Postgame Feedback

Every live event should persist:

- fired signals
- blocked signals
- missed signals detected after the fact
- fills and open-order lifecycle
- feed latency and stale periods
- PnL by sleeve
- strategy confidence changes
- recommended issue/doc/config updates

Postgame review should update signal confidence and backlog routing. Repeated missed live-window opportunities should become implementation issues, not repeated narrative comments.
