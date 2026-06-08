# Crypto Options Strategy Format Spec

Date: 2026-06-02

Status: reviewed sub-spec for modular strategy definitions and initial candidate catalog.

## 1. Purpose

The strategy format must let us define many strategy variants without rewriting the trading engine.

All variants are combinations of:

- profile-based signals
- event-based signals
- indicator-based signals
- outcome prediction
- hedge management
- scalping
- cashout
- hold-to-settlement

This spec defines the structure and the first candidate catalog for replay/pulse testing. Candidate inclusion is not live approval. Every candidate still has to pass strategy-spec validation, replay checks, pulse integrity checks, risk gates, and supervised executor boundaries before it can trade.

## 2. Strategy Spec Required Fields

Required fields:

- `strategy_id`
- `strategy_version`
- `strategy_family`
- `enabled`
- `budget_usd`
- `allowed_order_types`
- `signal_inputs`
- `signal_aggregation`
- `event_timing_rules`
- `indicator_timing_rules`
- `entry_rules`
- `exit_rules`
- `hedge_rules`
- `cashout_rules`
- `duplicate_exposure_rules`
- `risk_gates`
- `stop_gates`
- `replay_requirements`
- `live_pulse_requirements`
- `observability_fields`

## 3. Strategy Family Classification

Strategy family is descriptive and must not bypass validation.

Allowed broad families:

- `outcome_prediction`
- `outcome_prediction_with_hedge`
- `outcome_prediction_with_scalping`
- `outcome_prediction_with_cashout`
- `hedge_management`
- `scalping`
- `hybrid`

All families must still declare exact signal and execution mechanics.

## 4. Signal Input Section

The strategy must define profile inputs with:

- allowed profile types
- allowed grades
- activity gates
- buying-ahead handling
- aggregation window
- aggregation latency
- profile weighting
- conflict policy

The strategy must define event inputs with:

- event phase requirements
- time remaining rules
- target delta rules
- last-event outcome usage
- quote/spread/depth requirements

The strategy must define indicator inputs with:

- indicator id
- timing role
- usage role
- freshness limit
- blocker behavior
- confidence behavior

## 5. Entry Rules

Entry rules must declare:

- allowed order types
- side selection logic
- min/max entry price
- min/max spread
- quote freshness
- event phase
- time remaining
- size formula
- budget cap
- duplicate exposure rule
- re-entry rule

No entry rule can omit event/token identity.

## 6. Exit Rules

Exit rules must declare:

- cashout required or optional
- split exits allowed or forbidden
- hold-to-settlement allowed or forbidden
- late rescue behavior
- final-minute behavior
- SELL order type
- parent BUY linkage
- exit dedupe rule

Every strategy must declare lifecycle coverage even when it intends to hold to settlement.

## 7. Hedge Rules

If a strategy uses hedge management, it must declare:

- hedge target definition
- reference signal source
- side ratio calculation
- rebalance threshold
- max imbalance
- rebalance order type
- final-minute hedge behavior
- budget reserve
- exit/reconciliation behavior

Hedge management must operate on aggregate inventory, not random single-side signal rows.

## 8. Scalping Rules

If a strategy uses scalping, it must declare:

- expected fluctuation signal
- entry/exit quote requirements
- spread requirement
- target profit per leg
- re-entry conditions
- max active same-side exposure
- exit timeout
- final-minute disable behavior

Scalping must not be enabled just because profiles disagree. The strategy must declare why disagreement is expected to create tradable fluctuation.

## 9. Cashout Rules

If a strategy uses cashout, it must declare:

- fixed or dynamic cashout
- cashout target function
- win-rate/PnL dependency if dynamic
- split leg behavior
- quote freshness for SELL
- fallback when target is unreachable
- rescue behavior

Cashout rules must be testable independently of entries.

## 10. Strategy Spec Skeleton

```json
{
  "strategy_id": "placeholder_strategy_id",
  "strategy_version": "v1",
  "strategy_family": "outcome_prediction_with_cashout",
  "enabled": false,
  "budget_usd": 50.0,
  "allowed_order_types": ["market_buy", "limit_buy", "market_sell", "limit_sell"],
  "signal_inputs": {
    "profile": [],
    "event": [],
    "indicator": []
  },
  "signal_aggregation": {},
  "event_timing_rules": {},
  "indicator_timing_rules": {},
  "entry_rules": {},
  "exit_rules": {},
  "hedge_rules": {},
  "cashout_rules": {},
  "duplicate_exposure_rules": {},
  "risk_gates": {},
  "stop_gates": {},
  "replay_requirements": {},
  "live_pulse_requirements": {},
  "observability_fields": []
}
```

## 11. Initial Candidate Strategy Catalog

The first candidate set is designed to test every major component of the rebuilt system: profile signal routing, event context, indicator context, order lifecycle, reconciliation, cashout, hedge management, buying-ahead behavior, and replay compatibility.

These candidates are intentionally modular. A failed candidate should identify a specific signal or mechanic to reject or refine, not force a broad rewrite.

| Priority | Strategy id | Family | Primary signal inputs | Core mechanic | Components exercised |
| --- | --- | --- | --- | --- | --- |
| Starting 5 | `s_tier_outcome_consensus_cashout_v1` | `outcome_prediction_with_cashout` | S/S+/S++ `outcome_predictor` profiles, event context, quotes | Follow elite outcome-predictor consensus with dynamic cashout based on rolling win rate and PnL. | Profile consensus, grade gates, activity gates, market/limit BUY, cashout lifecycle, no-profile blocker, attribution. |
| Starting 5 | `hedger_ratio_replication_v1` | `outcome_prediction_with_hedge` | S/S+/S++ `hedger` and `grid_buyer` hedge-proportion generators | Track aggregate Up/Down inventory ratio of top profiles and rebalance our own aggregate position, never copy random single rows. | Hedge inventory, aggregate profile positions, ratio planner, budget reserve, market/limit rebalance, reconciliation. |
| Starting 5 | `grid_buyer_band_rebound_v1` | `hybrid` | S/S+/S++ `grid_buyer` band/rebound generators, event price stream, optional indicators | Treat clustered grid buying as rebound/support/resistance context, not direct directional prediction. | Grid interpretation, band/rebound signals, Polymarket YES/NO price stream, spread/depth gates, replay component scoring. |
| Starting 5 | `indicator_confirmed_outcome_v1` | `outcome_prediction` | Outcome-predictor consensus plus event and indicator context | Take profile outcome signals only when target-relative momentum, pressure, support/resistance, and timing context do not contradict the side. | Profile + event + indicator integration, target delta, indicator timing roles, blocker vs confidence behavior. |
| Starting 5 | `buying_ahead_pre_event_v1` | `outcome_prediction` | Profiles with `buying_ahead` behavior, future event universe, pre-event prices | Isolate pre-start profile buying to test whether early buyers predict event direction or create useful pre-event context. | Future event capture, profile-event timing links, pre-event phase handling, buying-ahead attribution, replay frames. |
| Secondary | `s_tier_outcome_hold_to_settlement_v1` | `outcome_prediction` | S/S+/S++ `outcome_predictor` consensus, event context | Follow elite outcome consensus with explicit hold-to-settlement coverage and minimal active exits. | Settlement lifecycle, hold coverage, realized vs mark-to-market PnL, no active SELL exception, late rescue policy. |
| Secondary | `a_fallback_outcome_probe_v1` | `outcome_prediction_with_cashout` | A-grade `outcome_predictor` profiles only when no S/S+/S++ outcome source is live | Test whether A fallback can preserve opportunity flow without silently weakening the main grade gate. | Fallback policy, grade attribution, no-S blocker behavior, conservative sizing, replay comparison against S-only mode. |
| Secondary | `profile_hedge_scalping_v1` | `outcome_prediction_with_hedge` | Hedge-proportion generators, event-price fluctuation, quotes | Maintain a hedge ratio while scalping tradable YES/NO fluctuations around that hedge. | Hedge + scalping interaction, split exits, duplicate exposure gates, active cost, lifecycle coverage per leg. |
| Secondary | `event_context_outcome_v1` | `outcome_prediction` | Event-only signals: last event outcome, target delta, time remaining, YES/NO price phase | Run a minimal event-context baseline without profile dependency to test the value of proprietary event signals alone. | Event universe, target delta, timing model, quote freshness, profile-independent blockers, replay baseline. |
| Secondary | `volatility_spread_scalping_probe_v1` | `scalping` | Event-token volatility, spread/depth, scalping metadata, optional indicators | Attempt limit-order scalping only when short-horizon volatility and liquidity make the expected spread capture executable. | Price stream latency, spread/depth gates, limit orders, unfilled/partial fills, exit timeouts, replay fillability. |

## 12. Starting Test Selection

Start with these five because together they cover the highest-value mechanics without depending on every speculative component at once:

1. `s_tier_outcome_consensus_cashout_v1`
2. `hedger_ratio_replication_v1`
3. `grid_buyer_band_rebound_v1`
4. `indicator_confirmed_outcome_v1`
5. `buying_ahead_pre_event_v1`

The starting set should run through replay first, then bounded pulse integrity tests. If replay shows a candidate has no data coverage, keep it in the catalog but mark it `blocked_for_data`, not rejected.

Selection rationale:

- `s_tier_outcome_consensus_cashout_v1` tests the direct profile signal path we most understand.
- `hedger_ratio_replication_v1` tests the corrected lesson from V3/V4: aggregate hedge state instead of random side-following.
- `grid_buyer_band_rebound_v1` tests whether grid profiles are useful as band/rebound context.
- `indicator_confirmed_outcome_v1` tests the new market/indicator infrastructure as a guard against profile-only failure modes.
- `buying_ahead_pre_event_v1` tests the newly discovered pre-event profile behavior and future-event capture.

## 13. Required Tests

Tests must cover:

- missing required fields fail validation
- unsupported order type fails validation
- signal input references known signal contracts
- initial candidate ids validate against the strategy spec schema
- profile type filter is enforced
- indicator timing rule is enforced
- entry rule requires event/token identity
- exit rule requires lifecycle coverage
- hedge rule requires aggregate inventory model
- scalping rule requires explicit exit coverage
- cashout rule can be tested independently
- disabled strategy cannot emit intents
- enabled strategy emits intents only through executor boundary
