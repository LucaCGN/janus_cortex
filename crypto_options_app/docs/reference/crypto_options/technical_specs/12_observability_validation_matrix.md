# Crypto Options Observability Validation Matrix

Date: 2026-06-03

Status: technical spec addendum for system integrity, order audit, and strategy coverage.

## Purpose

This document maps the trading-engine premises to concrete observability checks and the 10 candidate strategies. A strategy is not ready for longer comparison runs merely because it submitted a live-minimal order. Longer runs require evidence that the data, signal, strategy, intent, order, reconciliation, lifecycle, risk, and report layers are all observable.

## Strategy Legend

| Key | Strategy id |
| --- | --- |
| S1 | `s_tier_outcome_consensus_cashout_v1` |
| S2 | `hedger_ratio_replication_v1` |
| S3 | `grid_buyer_band_rebound_v1` |
| S4 | `indicator_confirmed_outcome_v1` |
| S5 | `buying_ahead_pre_event_v1` |
| S6 | `s_tier_outcome_hold_to_settlement_v1` |
| S7 | `a_fallback_outcome_probe_v1` |
| S8 | `profile_hedge_scalping_v1` |
| S9 | `event_context_outcome_v1` |
| S10 | `volatility_spread_scalping_probe_v1` |

## Feature Coverage Matrix

| Feature / logic to validate | Required evidence | Strategies |
| --- | --- | --- |
| Supervised runtime gate | Orders can only flow through executor boundary, ledger, risk, and reconciliation gates. | All |
| Market and limit order support | Both BUY and SELL order types are represented as intents and states before executor handoff. | All, especially S2, S8, S10 |
| BUY and SELL lifecycle | Candidate, intent, order, fill, position, exit, settlement, and PnL are separate state objects. | All |
| CLOB fill truth | Filled/unfilled/partial status comes from CLOB evidence, not assumed submission success. | All live candidates |
| Unfilled order handling | FOK/FAK no-fill is terminal `unfilled` and is not counted as a trade or position. | All live candidates |
| Partial-fill handling | Partial fills produce partial positions and partial lifecycle coverage. | S2, S8, S10 |
| Idempotency and dedupe | Duplicate intents, duplicate order submissions, and duplicate exits are blocked. | All |
| Lifecycle coverage | Every active BUY has cashout, hedge management, hold-to-settlement, rescue, or scalping coverage. | All |
| Cashout lifecycle | Dynamic cashout, SELL linkage, target metadata, and dedupe are observable. | S1, S7, S8, S10 |
| Hold-to-settlement lifecycle | No active SELL is allowed only with explicit settlement coverage. | S6 |
| Hedge aggregate inventory | Hedge/grid signals drive aggregate Up/Down ratio rebalancing, not random row following. | S2, S8 |
| Hedge-proportion generator | Hedger/grid profiles produce hedge-proportion context through generator contracts. | S2, S8, S3 |
| Band/rebound generator | Grid buyers produce support/resistance or rebound context, not direct direction by default. | S3 |
| Outcome expectation generator | Outcome predictors produce directional signal only through valid style/grade/usage gates. | S1, S4, S5, S6, S7 |
| A-grade fallback | A profiles are used only by explicit fallback strategy and only when S-tier source is absent. | S7 |
| Buying-ahead behavior | Future events and pre-start profile buys are captured and attributed. | S5 |
| Event-only baseline | Strategy can emit candidates from event context without profile dependency. | S9 |
| Indicator integration | EMA momentum, volume/pressure, support/resistance, and volatility inputs are fresh and attributed. | S4, S8, S10 |
| Target delta semantics | `target_delta_abs` is underlying price distance from event threshold. | S1, S4, S5, S6, S7, S9 |
| Time remaining and event phase | Pre-start, active, final-minute, expired, and post-settlement behavior are explicit. | All |
| YES/NO price phase | Option price phase can be used as event/scalping/hedge context. | S3, S8, S9, S10 |
| Quote freshness, spread, depth | Stale or non-executable quotes block order creation. | All |
| Risk gates | Budget, active cost, event exposure, correlated exposure, spread, liquidity, and slippage are observable. | All |
| Stop gates | Mechanical failures, stale service, drawdown, quality floor, and trade cap stop correctly. | All |
| Strategy isolation | Budgets, ledgers, blockers, and attribution remain per strategy. | All |
| Replay compatibility | Strategy can replay from contemporaneous stored frames or returns a structured blocker. | All |
| Canonical DB persistence | Candidates, intents, orders, fills, positions, exits, watermarks, and reports persist to canonical DB. | All longer tests |
| Exchange order audit | Local rows include token id, event slug, outcome, exchange order id, fill price/size, and reconciliation evidence. | All live candidates |
| One-cadence invariant | Health detects duplicate live cadence and stale service. | All system-wide |
| Health endpoint completeness | `/v1/crypto-options-app/health` exposes DB, feeds, strategies, validation artifacts, blockers, and readiness. | All system-wide |
| External exchange status | `/health.external_services.polymarket` exposes CLOB API status, active maintenance, incidents, and trading availability. | All supervised live runs |

## Health Endpoint Requirements

The monitor automation must use `GET /v1/crypto-options-app/health` as the simple system integrity endpoint. It must return JSON containing:

- schema version and UTC generation time
- read-only safety flags: `orders_allowed=false`, `live_trading_authorized=false`
- manual-order avoidance confirmation
- canonical DB existence, schema completeness, lifecycle table counts, and feed watermarks
- registered strategies and strategy families
- external Polymarket status, especially CLOB API trading availability
- latest live-validation artifacts and per-strategy statuses
- missing exchange-audit fields by strategy row
- readiness blockers before longer tests

`GET /v1/crypto-options-app/health/ping` is only a process liveness endpoint and must not be used as proof of strategy-system readiness.

## Current Integrity Finding

The 2026-06-03 live-minimal validation produced exchange-side BUY fills for the 10 candidates, but the older validation artifact rows did not persist full token IDs, exchange order IDs, fill price/size, or remote reconciliation evidence. Those rows can be sanity-checked against CLOB trades by price/size/time, but they cannot provide strong one-to-one audit proof.

The next validation must persist full exchange-audit fields and write lifecycle state into the canonical DB before standard group strategy comparison begins.

## Acceptance Criteria

- `/health` returns degraded status when the canonical DB is missing, lifecycle rows are absent, or validation artifacts lack exchange audit fields.
- `/health` returns degraded status when CLOB API is under maintenance, has active incidents, or the status provider is unavailable.
- Future live validations can be matched strongly to exchange trades by exchange order ID or token ID plus fill data.
- Any exchange-side trade not represented in local lifecycle state is surfaced as a reconciliation blocker.
- Longer comparison runs remain blocked until health reports no unresolved DB, lifecycle, or exchange-audit blockers.
- Exchange-status blocks are safety pauses, not strategy-performance failures. They must not be counted as losses or reconciliation mismatches unless exchange evidence indicates a possible fill.
