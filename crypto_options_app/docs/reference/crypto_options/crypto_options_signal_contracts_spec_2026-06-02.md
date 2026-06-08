# Crypto Options Signal Contracts Spec

Date: 2026-06-02

Status: reviewed sub-spec for strategy signal inputs.

## 1. Purpose

This spec defines the signal contracts available to strategy variants.

It does not pick final strategies. It defines how signals must be described, aggregated, timed, blocked, and tested.

## 2. Signal Contract Shape

Every signal component must declare:

- `signal_id`
- `signal_family`
- source table/view
- required fields
- freshness window
- aggregation window
- timing phase
- output side semantics
- confidence score semantics
- blocker conditions
- usage role
- replay availability
- test fixtures

Usage roles:

- `directional_signal`
- `hedge_proportion_signal`
- `band_rebound_signal`
- `volatility_liquidity_metadata`
- `cashout_context`
- `sizing_context`
- `blocker`
- `report_only`

## 3. Profile-Based Signals

Profile signals must be derived from the profile store and canonical event universe.

Allowed account type inputs:

- `outcome_predictor`
- `grid_buyer`
- `hedger`
- `scalping_trader`
- `unknown`

Every profile signal must specify:

- profile type filter
- grade filter
- activity recency gate
- buying-ahead inclusion/exclusion
- latency window
- event aggregation window
- side aggregation method
- conflict handling
- fallback behavior

Profile style is a hard routing input. A hedger/grid profile cannot be treated as a simple single-row outcome predictor.

## 4. Profile Signal Generator Roles

| Account type | Possible generator roles |
| --- | --- |
| `outcome_predictor` | directional outcome expectation |
| `grid_buyer` | band/rebound context, hedge proportion context |
| `hedger` | hedge proportion context, reconstructed outcome context |
| `scalping_trader` | volatility/liquidity metadata |
| `unknown` | report-only until classified |

These roles are possible roles, not strategy approval.

## 5. Profile Aggregation Requirements

Profile aggregation must define:

- aggregation interval: e.g. 5s, 10s, 20s, 60s
- account weight: grade, score, activity, generator quality
- action weight: notional, shares, event phase, side
- event matching: `event_slug`, `condition_id`, `token_id`
- duplicate row handling
- pre-event vs active-event separation
- stale row cutoff

Aggregation output must include:

- selected profiles
- ignored profiles
- side counts
- side weights
- disagreement/conflict metrics
- confidence
- blockers
- source rows used

## 6. Event-Based Signals

Event-based signals must come from canonical event and market rows.

Inputs:

- last event outcome
- event phase
- time remaining
- target delta absolute
- target delta signed for Up
- target delta signed for Down
- current YES price
- current NO price
- spread
- depth
- recent event-token price fluctuation
- buying-ahead flags

Event-based signals must specify whether they are:

- entry signals
- entry blockers
- sizing context
- exit context
- report-only context

## 7. Indicator-Based Signals

Indicator inputs:

- `target_relative_ema_momentum_v1`
- `volume_weighted_pressure_v1`
- `support_resistance_band_confluence_v1`
- `volatility_per_second_5m`
- `volatility_per_second_1h`
- `volatility_per_second_1d`

Each indicator must specify timing:

- pre-event only
- active-loop
- fixed event mark
- final-minute disabled
- report-only until replay proves utility

Examples:

- `volatility_per_second_1h` and `volatility_per_second_1d` are likely grounding context.
- `volatility_per_second_5m` may be reviewed at event marks.
- EMA momentum, volume pressure, and support/resistance may be active signal components if a strategy declares that role.

## 8. Freshness And Staleness

Every signal must declare freshness limits.

Required age fields:

- source row timestamp
- provider/chart timestamp if relevant
- system received timestamp
- system inserted timestamp
- decision timestamp
- quote age

Stale signal handling:

- block execution if the strategy requires freshness
- downgrade confidence if the strategy permits stale context
- record blocker/reason either way

## 9. Required Tests

Tests must cover:

- grade filter behavior
- profile type routing
- buying-ahead inclusion/exclusion
- profile aggregation dedupe
- stale profile row blocking
- stale event quote blocking
- missing event identity blocking
- target delta calculation
- indicator timing role enforcement
- profile conflict metrics
- no-profile blocker
- signal output schema validation
- replay frame compatibility
