# Crypto Options Strategy Manager Design

Date: 2026-06-02

Status: technical spec for strategy registry, validation, readiness, and promotion.

## Purpose

The strategy manager owns structured strategy definitions. It does not submit orders.

Strategies declare signals, timing, order types, exits, risk gates, replay requirements, pulse requirements, and observability.

## Strategy Spec Required Fields

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

## Registry State

Strategy state values:

- `draft`
- `validated`
- `replay_ready`
- `replay_rejected`
- `pulse_ready`
- `pulse_rejected`
- `standard_test_ready`
- `promoted`
- `disabled`

State transitions require recorded evidence.

## Initial Candidate Catalog

Starting 5:

1. `s_tier_outcome_consensus_cashout_v1`
2. `hedger_ratio_replication_v1`
3. `grid_buyer_band_rebound_v1`
4. `indicator_confirmed_outcome_v1`
5. `buying_ahead_pre_event_v1`

Secondary 5:

6. `s_tier_outcome_hold_to_settlement_v1`
7. `a_fallback_outcome_probe_v1`
8. `profile_hedge_scalping_v1`
9. `event_context_outcome_v1`
10. `volatility_spread_scalping_probe_v1`

Candidate inclusion is not live approval.

## Readiness Checks

Replay readiness requires:

- valid strategy schema
- known signal contracts
- available data coverage or structured data blocker
- risk gates declared
- lifecycle coverage declared
- replay frame compatibility

Pulse readiness requires:

- replay not rejected
- executor boundary configured
- supervised runtime gates configured
- no missing lifecycle coverage
- risk/stop gates active
- one-live-cadence invariant

## Promotion Rules

A strategy can be promoted only with:

- validated schema
- replay report
- pulse integrity report
- standard test report
- no unresolved reconciliation issues
- drawdown inside gates
- strategy-specific performance threshold met

## Acceptance Criteria

- All 10 candidates exist as specs or registry records.
- Disabled strategies cannot emit intents.
- State transitions require evidence.
- No strategy can bypass risk, lifecycle, or reconciliation requirements.

## P2 Centralization Update

Rotation9 adds managed variants `hedger_ratio_replication_v4`, `profile_hedge_scalping_v4`, and `grid_band_rebound_v3`. These must not be treated as unchanged v3 reruns. They require executable profile-pressure gates, price-path context, and managed runtime readiness before live comparison.
