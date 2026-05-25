# StrategyPlan Entry Timing Template Guidance

Status: current guidance
Updated: 2026-05-25
Owning issue: https://github.com/LucaCGN/janus_cortex/issues/55

## Scope

This document converts the `#55` entry-timing replay work into StrategyPlan template guidance. It is guidance for future reviewed plans, not a runtime config update and not live-order permission.

Primary evidence:

- `local/shared/artifacts/entry-timing-research/2026-05-25/entry_timing_matrix_20260525T072156Z.json`
- `local/shared/artifacts/entry-timing-research/2026-05-25/entry_timing_event_control_recommendation_pack_20260525T072156Z.json`
- `local/shared/reports/daily-live-validation/entry_timing_matrix_20260525T072156Z.md`
- `local/shared/reports/daily-live-validation/entry_timing_event_control_recommendation_pack_20260525T072156Z.md`

Hard boundary:

- Do not mutate `event-controls/current.json` from this guidance.
- Do not change StrategyPlan defaults from this guidance alone.
- Do not place, cancel, replace, submit, sign, broadcast, redeem, or start workers from this guidance.
- Any live promotion still requires fresh Janus StrategyPlan, evaluate/execute, direct CLOB, feed, worker, risk, kill-switch, and explicit operator/Janus gates.

## Evidence Summary

The current replay stack separates timing policy, fillability, cancellation/expiry, adverse selection, missed-entry cost, and final-score outcome accounting.

| Timing policy | Current guidance | Evidence state |
|---|---|---|
| `pregame_resting_limit_order` | Disabled by default for live templates unless order-lifecycle replay proves the order survives event start or a plan includes explicit expiry/cancel/recheck handling. | No current fixture row proves pregame resting orders survived event-start cancellation. |
| `first_live_window_after_event_start` | Review-only WNBA low-band candidate when all direct gates are fresh and the plan is deliberately one-shot. | Dallas and Seattle candidates produced fillable low-band evidence; live promotion remains false. |
| `immediate_live_low_band_rebound` | Review-only WNBA low-band candidate for specific score/price regimes, with no inventory adding and a conservative max-entry ceiling. | Atlanta candidate produced positive replay evidence at `0.32`; live promotion remains false. |
| `post_q1_context_entry` | Preferred over blind pregame entry when the plan needs score, pace, injury, market repricing, or liquidity context before entry. | Side-by-side replay makes Q1 context a better template default than unreviewed pregame resting, but promotion still needs current event gates. |
| `post_q1_stability_confirmed_entry` | Preferred StrategyPlan template for controlled entries when enough consecutive price/orderbook stability exists. | Atlanta and Dallas had stability-confirmed fills; Seattle did not meet the consecutive stability requirement in the current replay. |
| `late_game_min_price_add` | Quarantined unless an independent positive replay clears duplicate cooldown and final-score edge blockers. | Thunder Q4 subpenny/min-price is a negative control with duplicate intent and final-score negative-edge blockers. |

## Template Rules

### Pregame Resting Limit Order

Use only when all of these are explicit in the reviewed StrategyPlan:

- event-start expiry policy is present;
- direct CLOB recheck is required after scheduled start;
- missing direct order after start becomes `event_start_expired_orders`, not unresolved exposure;
- no duplicate buy can fire until direct CLOB open orders, fills, and local pending intents are reconciled;
- the plan records the target/stop/rebuy policy that applies if the order survives or fills.

Default posture:

```json
{
  "entry_timing_policy": "pregame_resting_limit_order",
  "entry_enabled": false,
  "requires_event_start_recheck": true,
  "event_start_missing_order_status": "event_start_expired_orders",
  "promotion_status": "disabled_until_order_lifecycle_replay_proves_survival"
}
```

### First Live Window

Use only as a reviewed, one-shot controlled live test candidate. It is not a repeating grid default.

Required StrategyPlan fields or equivalent entry rules:

- fresh score, period, clock, and feed timestamp;
- direct CLOB bid/ask/spread/depth for the selected outcome;
- event-scoped direct inventory is clear;
- no active pending intent or open buy for the same event/outcome/sleeve;
- `allow_inventory_adding=false` unless a separate reviewed plan explicitly permits adding;
- `cooldown_seconds>=90`;
- max entry price is case-specific, not a global WNBA ceiling.

Current WNBA review-only ceilings:

| Event | Side | Max entry |
|---|---|---:|
| `wnba-phx-atl-2026-05-24` | Atlanta | `0.32` |
| `wnba-dal-nyl-2026-05-24` | Dallas | `0.23` |
| `wnba-wsh-sea-2026-05-24` | Seattle | `0.26` |

The broader WNBA ceiling remains `0.45` as a cap for candidate review, not a permission to enter any WNBA side under `0.45`.

### Post-Q1 Context Entry

Prefer this policy when the event thesis depends on early-game evidence:

- score-gap band after Q1;
- favorite/underdog repricing after live play begins;
- player-status or usage confirmation;
- market spread/depth after event-start liquidity changes;
- stale pregame order status that needs direct CLOB reproof.

Default posture:

```json
{
  "entry_timing_policy": "post_q1_context_entry",
  "entry_enabled": "review_required",
  "requires_score_gap_evidence": true,
  "requires_direct_clob_recheck": true,
  "requires_pending_intent_clearance": true
}
```

### Post-Q1 Stability-Confirmed Entry

Use this as the preferred controlled-entry template when the event has a low-band candidate and the replay/runtime evidence can prove consecutive stable windows.

Required evidence:

- at least two consecutive current-event price/orderbook observations inside the allowed entry band;
- no stale feed or stale CLOB blocker;
- no spread/depth blocker for the strategy family being used;
- no direct event-scoped open order, position, or pending intent for the same side/sleeve;
- target and stop policy is present before any execution path.

Default posture:

```json
{
  "entry_timing_policy": "post_q1_stability_confirmed_entry",
  "entry_enabled": "review_required",
  "stability_window_count_min": 2,
  "duplicate_intent_cooldown_required": true,
  "target_policy_required": true,
  "stop_policy_required": true
}
```

### Late-Game No-Bid Or Min-Price Add

Keep this disabled by default.

The OKC/SAS Thunder Q4 case is the current negative control. It showed attractive subpenny or minimum-price fluctuations, but the fixture ended as a final-score loser and duplicated late intent behavior. A future lottery sleeve must be separate from ordinary grid/scalp logic and must clear independent replay before any event-control or StrategyPlan promotion.

Default posture:

```json
{
  "entry_timing_policy": "late_game_min_price_add",
  "entry_enabled": false,
  "no_bid_min_price_lottery_v1": false,
  "q4_subpenny_hype_bounce": false,
  "min_price_lottery_allowed": false,
  "required_blocker_clearance": [
    "duplicate_intent_cooldown_required",
    "final_score_negative_edge",
    "independent_positive_replay_before_unquarantine"
  ]
}
```

## StrategyPlan Authoring Checklist

Every future StrategyPlan using this guidance should state:

1. `entry_timing_policy`.
2. Whether the policy is `disabled`, `review_required`, or `enabled_after_gates`.
3. The case-specific max entry price and source evidence.
4. The event-start expiry and direct CLOB recheck policy.
5. Pending-intent and duplicate-cooldown behavior.
6. Target, stop, and rebuy posture.
7. Whether event-control readback review is required.
8. The exact fresh gates needed before live execution.

Minimum fresh gates:

- current StrategyPlanJSON for the event;
- current event/market/outcome/token mapping;
- fresh scoreboard and feed metadata;
- fresh direct CLOB book and event-scoped inventory;
- aligned live worker scope and heartbeat;
- risk budget and kill switch green;
- explicit operator and Janus approval;
- Janus evaluate/execute path, not raw exchange bypass.

## Closure Note For Issue 55

The `#55` acceptance criteria are satisfied as research guidance when this document is paired with the current matrix and recommendation artifacts. Future work should not keep adding generic `#55` comments. Remaining implementation belongs to focused runtime issues:

- `#62` for the next controlled WNBA live lifecycle.
- `#63` for live-worker adoption of aggregation, event budgets, and lot-level target management.
- `#70` for replay-first no-bid/min-price calibration.
- `#69` only for reviewed event-control readback or update flows.
