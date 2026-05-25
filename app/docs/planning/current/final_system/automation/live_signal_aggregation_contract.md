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

## Trigger-To-Sleeve Binding

The live tick must bind triggers to sleeves before aggregation. This is the missing connection between the tick-loop trigger box and the sleeve model.

Binding responsibilities:

- convert StrategyPlan sleeve states into `buy`, `sell`, `block`, or `monitor` signals with `sleeve_id`, `sleeve_group`, `sleeve_role`, `strategy_id`, and strategy family metadata;
- convert paired microcycle next-leg evidence into sleeve-scoped `sell` or `rebuy` signals with `cycle_id`, token, outcome, target price, and requested shares;
- convert paired microcycle duplicate-buy protections into local sleeve `block` signals, not global event blockers;
- preserve LLM/runtime triggers as monitor evidence unless a reviewed sleeve explicitly promotes them into an actionable signal;
- carry binding metadata through aggregation into each order-intent candidate so Janus can tell which sleeve/cycle/trigger produced the next proposed action.

Current implementation:

- `app/modules/agentic/sleeve_trigger_binding.py` emits `sleeve_trigger_binding_evidence_v1`;
- `codex_tool/run_live_strategy_tick.py` stores the binding evidence in `market_state["sleeve_trigger_binding"]` and `live_signal_aggregation["sleeve_trigger_binding"]`;
- `app/modules/agentic/signal_aggregation.py` preserves `sleeve_id`, `sleeve_group`, `sleeve_role`, `strategy_id`, `strategy_family`, `cycle_id`, `trigger_type`, and `trigger_source` on order-intent candidates.

## Gate Scope

Strategy gates are local to the signal source or sleeve that owns them.

A score-gap limit, band-retest rule, spread threshold, no-bid condition, LLM-cost limit, or strategy-level max/min should not become a global no-trade result unless it is a live-money safety gate shared by every sleeve. The aggregator should preserve the local blocker and continue evaluating independent signals that still have valid feed, CLOB, inventory, budget, and risk evidence.

Examples:

- A band/grid sleeve blocked by `score_gap_outside_range` does not automatically block a core-hold reduce signal.
- A no-bid/min-price lottery component blocked as replay-only does not block ordinary target replacement for an existing filled lot.
- A missing or stale LLM revision is advisory unless the selected sleeve explicitly requires LLM review.
- A global kill switch, stale direct CLOB truth, missing token mapping, or event budget exhaustion blocks every live execution candidate.

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

## No-Bid And Min-Price Lottery Component

Low-price comeback and hype-bounce behavior is a candidate component, not a live default.

Rules:

1. No-bid or ask-only periods must be recorded as a distinct fillability regime.
2. A 1c-4c price range is not automatically an edge; replay must prove that fills can occur before targetable rebounds and that duplicate cooldown does not erase the opportunity.
3. Subcent/min-price behavior should stay `replay_only` until postgame evidence promotes it through event-control config with explicit caps.
4. Live promotion requires a component-specific cap, cooldown, target policy, and final-score outcome accounting.
5. While replay-only, this component can emit `monitor`, `blocked`, or `review_candidate` signals but cannot suppress other sleeves or place live order intents.

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

## Lot-Level Target Management

Sports-live execution must manage filled lots and sleeve targets, not just a single event-level position flag.

Every reconciled fill should have:

- source: Janus, operator/manual, or imported external;
- event id, token id, outcome, side, share count, price, timestamp, and external order/trade id when available;
- sleeve assignment: grid/scalp, core hold, rebuy, reduce/stop, or imported/manual;
- weighted basis for the sleeve and for the aggregate event position;
- target, stop, rebuy, or monitor policy;
- current direct CLOB coverage: matching open sell orders, filled targets, stale targets, and target-missing shares.

Target coverage must be evaluated from fresh direct CLOB account truth:

- Open sell orders below or unrelated to the current weighted basis do not make a sleeve target-covered.
- Manual/operator fills must be reconciled before Janus computes new target ladders.
- Target replacement should cancel/replace only through approved Janus order-management gates and only when event controls allow it.
- If a strategy wants both grid and core exposure, the system should be able to target only the grid lot while holding the core lot.

## Paired Microcycle Order Engine

Lot evidence is not the same thing as a trading cycle.

Grid/scalp sleeves need a paired microcycle state machine so Janus does not keep buying the same outcome without managing the corresponding sell/rebuy legs. A microcycle is one bounded buy/sell/rebuy loop for one token, sleeve, band, and size.

Each cycle should track:

- `cycle_id`, event id, sleeve id, token id, outcome, band id, and strategy id;
- buy leg: requested price, filled price, shares, order id, fill id, status;
- paired sell leg: target price, submitted/open/filled/stale status, order id, fill id;
- optional paired rebuy leg: trigger price, review status, submitted/open/filled status;
- cap state: max active cycles for the sleeve, max shares per cycle, max unresolved buy legs, max unresolved sell legs, and event budget remaining;
- freshness state: last direct CLOB reconciliation, scoreboard/clock evidence, and event-control version;
- execution boundary: evidence-only, intent-candidate, or submitted-through-Janus-gates.

Required behavior:

1. When a buy leg fills, the next eligible action is the paired sell leg, not another same-cycle buy.
2. While the paired sell leg is open, pending, missing, or stale, the cycle blocks duplicate same-cycle buys.
3. When the paired sell leg fills, the cycle can close or request a paired rebuy only after fresh score/CLOB/event-control review.
4. Multiple cycles can run only when they are distinct by band/sleeve and event controls allow parallel cycles.
5. Core-hold sleeves are not cycled unless explicitly converted to a grid/scalp sleeve.
6. Operator/manual fills are imported into cycle state before Janus computes the next leg.
7. The engine must emit blockers such as `paired_sell_open_blocks_duplicate_buy`, `paired_sell_missing`, `paired_rebuy_requires_fresh_review`, `cycle_budget_exhausted`, and `cycle_fill_reconciliation_missing`.

This is the behavior required for 1c-3c interval trading: the system should place or recommend the opposite leg as soon as a fill is detected, then wait for that opposite leg to resolve before adding the next linked exposure.

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
