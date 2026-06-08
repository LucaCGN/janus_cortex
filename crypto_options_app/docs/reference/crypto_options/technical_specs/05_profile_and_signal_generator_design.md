# Crypto Options Profile And Signal Generator Design

Date: 2026-06-02

Status: technical spec for profile grading and signal generation.

## Purpose

Define how profiles become reliable signal generators without confusing activity, quality, and account style.

Recent activity controls usage eligibility. It must not inflate grade.

## Profile Flow

1. Normalize profile refs.
2. Fetch account activity asynchronously.
3. Persist raw activity.
4. Link activity to canonical event/token rows.
5. Reconstruct event-level profile behavior.
6. Classify account style from reconstructed event behavior.
7. Compute global profile grade.
8. Compute generator-specific scores.
9. Publish signal generator outputs for strategies.

## Grading Inputs

Global grade uses:

- PnL by period: 1h, 1d, 7d, 30d, all time.
- Closed or reconstructed win rate by period: 1h, 1d, 7d, 30d, all time.
- Closed or reconstructed return percentage by period: 1h, 1d, 7d, 30d, all time.
- Bot/frequency behavior.
- Crypto event coverage.
- Recent crypto share.
- Style quality metrics.

Usage gates use:

- active in last 5 minutes
- active most of last hour
- crypto events in last 1h and 24h
- style eligibility for the target generator

## Account Types

| Type | Classification basis | Generator roles |
| --- | --- | --- |
| `outcome_predictor` | Reconstructed events mostly one-side directional exposure. | outcome expectation |
| `hedger` | Reconstructed events show meaningful both-side exposure and controlled effective PnL. | hedge proportion, reconstructed outcome expectation |
| `grid_buyer` | Many buys across price levels and sides, clustered around bands. | band/rebound, hedge proportion |
| `scalping_trader` | Buy/sell cycles, high turnover, short-horizon exits. | volatility/liquidity metadata |
| `unknown` | Insufficient or conflicting reconstruction. | report-only |

## Event-Level Reconstruction

Reconstruction is mandatory before using grid, hedger, or scalping profiles as anything beyond report-only.

Per profile/event reconstruction must compute:

- Up/Down raw orders
- buys and sells by side
- share count by side
- cost basis by side
- weighted average price by side
- realized PnL from observable sells
- settlement PnL when event result is known
- effective event win/loss
- side skew
- hedge balance
- price-band coverage
- turnover
- reconstruction quality

## Signal Generators

| Generator | Profile types | Output |
| --- | --- | --- |
| `outcome_expectation` | `outcome_predictor`, reconstructed `hedger` | expected side, confidence, source weights, conflict score |
| `hedge_proportion` | `hedger`, `grid_buyer` | target Up/Down ratio, confidence, event phase, imbalance hints |
| `band_rebound` | `grid_buyer` | support/resistance/rebound context from clustered option-price buying |
| `volatility_liquidity` | `scalping_trader` | short-horizon fluctuation and liquidity metadata |
| `buying_ahead` | any classified profile with pre-start BUY behavior | pre-event side, timing, profile weight, outcome history |
| `top_profiles_distribution` | S/S+/S++ `outcome_predictor`, `hedger`, `grid_buyer`, optional explicitly configured A fallback | Up/Down distribution variants by cost, shares, and profile count |

## Strategy Usage Rules

- Strategies declare which generator roles they consume.
- A profile style cannot be silently reinterpreted by a strategy.
- Low-confidence reconstructed events cannot promote a profile to S/S+/S++.
- A-grade fallback is a strategy decision, not a global grade rule.
- B/C/D/E/U profiles can be stored and researched but must not be used by a strategy unless that strategy explicitly declares research-only replay usage.

## Acceptance Criteria

- Global grade, usage eligibility, and generator score are separate fields.
- Account type is derived from reconstructed event behavior.
- Every generator output has source rows and confidence.
- Strategies can audit why a profile was used or ignored.

## P2 Profile Distribution Update

`top_profiles_distribution` is now a durable data-service output, not a transient monitor artifact. The service writes one profile distribution snapshot per relevant current/future event plus component rows explaining each profile/outcome contribution. The canonical method is `cost_weighted`, with `shares_weighted` and `profile_count_weighted` variants persisted in the same payload.

Source modes:

- `stream_orders`: future primary path from real-time activity trade/order-match rows.
- `batch_activity`: wallet activity/trade rows persisted into `profile_raw_activity` and `profile_event_orders`.
- `position_snapshot_fallback`: current profile position snapshots persisted into `profile_event_positions`.

Strategies must consume the persisted snapshot/readiness payload and apply their own staleness and risk gates before converting it into hedge or outcome behavior.
