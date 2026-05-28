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
- `app/modules/agentic/engine.py` can promote `live_signal_aggregation.decision.order_intent_candidates` into Janus `OrderIntent`s during StrategyPlan evaluate/execute, with duplicate protection against normal StrategyPlan intents.
- `app/modules/agentic/live_game_context.py` emits `live_game_context_evidence_v1`, which attaches game scenario classification, per-sleeve ML/PBP confidence readback, realized-profit risk state, and bounded standalone opportunistic candidates to the same aggregation surface.

Promotion rules:

- `buy` and `rebuy` candidates become buy limit intents.
- `sell` and `reduce` candidates become sell limit intents.
- required fields are event/market/outcome/token, sleeve, side, price, size, and reason metadata.
- minimum size, minimum buy notional, max buy notional, `max_intents`, and global live gates still apply.
- candidates matching an already-created StrategyPlan intent for the same token/side/sleeve/cycle are blocked as duplicates, not submitted twice.
- standalone opportunistic buy candidates may promote only when they declare a paired lifecycle policy directly on the candidate; without target/stop/hold policy they remain blocked by `paired_lifecycle_policy_required_for_buy`.

## Live Game Context Evidence

Every live tick should emit event-level context before aggregation. This is the bridge between game state, sleeve choice, ML/PBP confidence, and dynamic risk.

Current implementation: `app/modules/agentic/live_game_context.py`.

The evidence includes:

| Field | Purpose |
|---|---|
| `game_scenario` | S/A/B/C/D classifier result from scoreboard, period, clock, score gap, player shock, price, and PBP/run tags. |
| `classification_snapshot` | The exact normalized inputs used by the classifier. |
| `sleeve_candidate_review` | Scenario-derived sleeve suggestions plus duplicate checks against the active StrategyPlan. |
| `ml_confidence_by_sleeve` | Per-sleeve confidence readback from the scenario classifier and PBP annotation. PBP annotation uses deterministic fallback when dispatch is off, and can call the optional `gpt-5.4-nano` dispatcher when live LLM dispatch and credentials are enabled. |
| `dynamic_risk_state` | Profit-ratcheted risk state from realized event/day PnL, unresolved inventory, scenario level, liquidity, and latency. Aggregation event-budget readback consumes this state so realized profit can fund bounded addon risk and unresolved loss exposure can cut event cap. |
| `opportunistic_signal_candidates` | Standalone candidate rows for valid entry/exit points not covered by current sleeves. |

The context artifact must not become a global blocker by itself. Scenario `D/U`, stale feeds, or unresolved inventory can block context-originated standalone candidates, but local sleeve signals still continue through their own gates unless a true global safety gate fails.

## Standalone Opportunistic Signals

Janus may need to react to a valid entry/exit point that no currently configured sleeve captured. This is allowed only as a bounded aggregation candidate, not as an executor bypass.

Required controls:

1. The candidate must be generated from fresh normalized game/CLOB/account evidence.
2. The game scenario must not be `D` or `U`.
3. The candidate must be funded by realized-profit risk budget or another explicit event-control budget, not by open unrealized profit.
4. The candidate must include a lifecycle policy: target, stop, hold reason, and rebuy review behavior.
5. StrategyPlan evaluate/execute must still enforce minimum size, minimum buy notional, max buy notional, event budget, direct-truth, kill-switch, worker, and order-management gates.

Current implementation starts with realized-profit-funded underdog/opportunistic entries. It is intentionally narrow: if realized profit is too small to fund the exchange minimum, the artifact records `realized_profit_opportunistic_budget_below_minimum` instead of creating an order-intent candidate.

## Nano PBP Annotation

The cheap PBP annotation lane is evidence-only. `app/modules/agentic/pbp_annotation.py` emits `pbp_annotation_evidence_v1` for every live tick with deterministic tags, model tier, dispatch status, and escalation hints.

When `codex_tool/run_live_strategy_tick.py` runs with LLM dispatch enabled and runtime credentials are available, it resolves a `gpt-5.4-nano` dispatcher. Nano output may add compact tags and mark a window for mini/frontier review, but it must not place orders. Aggregation can consume the resulting evidence shape; StrategyPlan/live-worker/order-management gates remain the only order path.

## Reduce/Stop Lifecycle Evidence

The May 27 WNBA slate proved that target-only lifecycle is insufficient. Entry, paired target, and position-limit blockers worked, but losing residual inventory survived because stop metadata was not an active deterministic state machine.

Current implementation: `app/modules/agentic/reduce_stop_lifecycle.py`.

Every live tick now records `sports_live_reduce_stop_lifecycle_evidence_v1` under `market_state["reduce_stop_lifecycle"]` and `live_signal_aggregation["reduce_stop_lifecycle"]`.

The evidence evaluates, per sleeve:

- direct event-scoped inventory and weighted basis;
- current direct CLOB exit price;
- StrategyPlan `stop_rules` such as `stop_price`, `max_adverse_cents`, and `max_loss_cents`;
- target management status: covered, missing, stale, or final cleanup;
- live game scenario: Q4/endgame, garbage-time/falling-knife, final state, or normal target management;
- rebuy permission after adverse thesis failure.

When fresh evidence shows `reduce_stop_triggered`, `q4_endgame_loss_mode`, `adverse_thesis_failed`, or `target_uncovered_reduce_review`, the lifecycle module emits a `reduce` signal. That signal can become a sell `OrderIntent` only through the normal Janus StrategyPlan/live-worker/order-management gates. Final-state rows are cleanup/reconciliation evidence and must not create new target orders.

Exit signals have priority over new `buy`/`rebuy` candidates for the same sleeve when they carry reduce/stop/Q4/adverse-thesis reason codes. This prevents duplicate rebuy loops after a failed thesis while preserving unrelated sleeves.

## Gate Scope

Strategy gates are local to the signal source or sleeve that owns them.

A score-gap limit, band-retest rule, spread threshold, no-bid condition, LLM-cost limit, or strategy-level max/min should not become a global no-trade result unless it is a live-money safety gate shared by every sleeve. The aggregator should preserve the local blocker and continue evaluating independent signals that still have valid feed, CLOB, inventory, budget, and risk evidence.

Examples:

- A band/grid sleeve blocked by `score_gap_outside_range` does not automatically block a core-hold reduce signal.
- A no-bid/min-price lottery component blocked as replay-only does not block ordinary target replacement for an existing filled lot.
- A missing or stale LLM revision is advisory unless the selected sleeve explicitly requires LLM review.
- A global kill switch, stale direct CLOB truth, missing token mapping, or event budget exhaustion blocks every live execution candidate.
- A reduce/stop or final-cleanup blocker must never be suppressed by `position_limit_reached`, controlled-entry caps, or price-band entry gates.

2026-05-27 OKC/SAS final readback added one more gate-scope rule: a token/outcome-level existing position is not automatically a global same-side blocker. Explicitly reviewed add-down sleeves may set `position_limit_scope=sleeve` plus `allow_existing_position_add=true`; this lets the sleeve reach event/sleeve budget and paired-exit evaluation while pending Janus intents, direct-truth gates, spread/fillability, kill switch, and event caps remain strict blockers. Default sleeves remain conservative unless they opt in.

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

## Risk Mode Budget Ladder

Sports-live budgets are policy-derived, not fixed nominal constants. The current `$10/game` development target is expressed as the minimum of portfolio percentage, cash percentage, and nominal cap so it naturally shrinks when available cash falls and can grow only after the policy is deliberately raised.

Canonical modes:

| Mode | Purpose | Event cap | Active cycle posture |
|---|---|---|---|
| `validation` | Controlled minimum-size live verification. | `min(3% portfolio, 10% cash, $5)` | One active cycle, stricter expected edge. |
| `development` | Today's learning/live-testing money profile. | `min(10% portfolio, 20% cash, $10)` | Up to four active cycles and up to five concurrent events, with sleeve/side caps still local. |
| `production` | Conservative deployed-money profile. | `min(2% portfolio, 5% cash, $5)` | Fewer cycles and higher expected edge threshold. |

Mode policy lives in `app/modules/agentic/event_budget.py` via `build_event_risk_budget_policy`. Every live StrategyPlan or worker tick may override a cap only by persisting the override in event controls or StrategyPlan metadata. Chat text, screenshots, or stale pregame priors do not change the risk mode.

Risk caps are local unless they are true safety gates:

- global blockers: kill switch, stale direct CLOB truth, missing token mapping, and exhausted event budget;
- local blockers: side cap, phase cap, sleeve cap, same-side exposure cap, and max active cycles for that sleeve/cycle family.

This keeps one blocked side, phase, or sleeve from suppressing unrelated sleeves. It also allows future postgame optimization to test `50/50`, favorite-heavy, underdog-heavy, one-sided, delayed, or phase-specific budget splits without changing the execution boundary.

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

The canonical event-level artifact is `postgame_evaluation.json` as tracked by [#78](https://github.com/LucaCGN/janus_cortex/issues/78). It must separate source authority:

1. account-scoped direct CLOB fills and Janus order reconciliation;
2. local Janus DB order/trade lifecycle;
3. direct current-event positions and open orders;
4. token-level direct CLOB market tape for price path and fillability only;
5. Polymarket UI screenshots for displayed rounding/operator audit only.

Token-level market tape rows such as `current_token_trades` must not be reported as account PnL unless matched to known external order ids. Any metric derived from non-account sources must carry a source label such as `clob_market_tape`, `ui_observed`, or `inferred`.

Every complete postgame artifact should run four comparable evaluations over the same tick stream:

| Mode | Purpose |
|---|---|
| `realized_live` | What actually happened through Janus/operator/account-scoped fills, open orders, final positions, and settlement assumptions. |
| `sleeve_isolated` | How each sleeve would have performed alone with the same event budget and simulated fills from recorded direct CLOB prices. |
| `aggregate_replay` | How all sleeves would have performed together through the current aggregator, budget, risk, and dedupe rules. |
| `leave_one_out` | Aggregate replay minus one sleeve, used to measure marginal sleeve value and detect sleeves that suppress or improve portfolio performance. |

`leave_one_out` is not only a reporting view. Its output should feed #79 sleeve-portfolio recommendations: side-budget splits, phase-budget changes, selected-side/contrarian modes, and whether strategies should run sequentially or in parallel.

Every complete P1/P2 artifact must also expose automation-ready summary sections, not only raw nested replay data:

| Section | Purpose |
|---|---|
| `mode_comparison` | One compact comparison table for `realized_live`, `sleeve_isolated`, `aggregate_replay`, and `leave_one_out`, including source confidence and account-PnL eligibility. |
| `sleeve_scoreboard` | Per-sleeve blocker, fillability, simulated PnL, missed-window, marginal-value, and next-action rows. |
| `why_no_trade` | Event/sleeve blocker diagnosis with global-gate versus local-sleeve scope counts. |
| `strategy_promotion_review` | Promotion/demotion gate that remains disabled when realized lifecycle evidence is unresolved, replay PnL is negative/missing, or live blockers remain. |

2026-05-27 implementation proof: `local/shared/artifacts/ops/2026-05-26/postgame-review_20260527T100457Z.json` contains all four sections for OKC/SAS. It records `known_cashflow_usd=-6.00`, aggregate replay PnL `-2.00`, all six sleeves blocked, `global_gate=4080`, `local_sleeve=1521`, and `automation_ready=false` because final realized lifecycle evidence remains unresolved.

Postgame review should update signal confidence, event-control recommendations, issue tasks, and backlog routing. Repeated missed live-window opportunities should become implementation issues or local task rows, not repeated narrative comments.
