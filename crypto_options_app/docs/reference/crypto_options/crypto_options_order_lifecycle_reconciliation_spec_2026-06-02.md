# Crypto Options Order Lifecycle And Reconciliation Spec

Date: 2026-06-02

Status: reviewed sub-spec for trading-engine mechanics.

## 1. Purpose

The order lifecycle module turns strategy intents into reconciled trading state.

It must never assume that submission equals fill, or that fill equals settled PnL.

## 2. State Objects

The engine must maintain separate state objects:

| Object | Meaning |
| --- | --- |
| Candidate | Strategy-observed opportunity before execution checks. |
| Intent | Idempotent strategy request to attempt an order. |
| Order | Submitted executor request with exchange-facing metadata. |
| Fill | Reconciled filled or partially filled quantity. |
| Position | Open exposure created by fills. |
| Exit | SELL/cashout/settlement lifecycle for a position. |
| Settlement | Final event outcome and realized value. |
| PnL | Realized, unrealized, active cost, and budget impact. |

## 3. Order Type Support

The executor boundary must support:

- market BUY
- market SELL
- limit BUY
- limit SELL

Each strategy declares allowed order types.

If a strategy allows only limit orders, market conversion is forbidden. If a strategy allows market orders, it must still pass quote freshness, spread, slippage, and budget gates.

## 4. Idempotency

Every candidate, intent, order, fill, exit, and settlement row must have a stable idempotency key.

Intent key inputs should include:

- strategy id
- run id
- event identity
- token id
- outcome
- side
- intent type
- profile/source attribution
- decision timestamp bucket
- target order size/price

Duplicate order submissions for the same intent are mechanical failures unless explicitly marked as a retry of the same idempotency key.

## 5. Fill And Unfill States

Supported order states:

- `created`
- `submitted`
- `accepted`
- `rejected`
- `partially_filled`
- `filled`
- `unfilled`
- `expired`
- `cancelled`
- `submit_error`
- `reconciliation_pending`
- `reconciliation_failed`

Rules:

- Unfilled orders do not count as trades.
- Partially filled orders create partial positions.
- Submitted orders without reconciliation do not create positions.
- Rejected orders must preserve rejection reason and source payload.
- Expired/cancelled orders must not leave active exposure.

## 6. Reconciliation

Reconciliation must compare local ledger state with Polymarket CLOB/order state.

Required reconciliation checks:

- submitted order exists remotely or has a terminal error
- remote filled size matches local filled size within dust tolerance
- open exposure matches local active position
- SELL/cashout state matches local exit lifecycle
- candidate ledger and raw ledger agree
- executor/supervisor artifacts agree

Ledger state is authoritative only after reconciliation.

## 7. Position Lifecycle Coverage

Every active BUY fill must have explicit lifecycle coverage.

Allowed coverage types:

- active cashout order
- split exit plan
- hedge-managed inventory plan
- hold-to-settlement policy
- late rescue policy
- reconciled settlement state

Missing lifecycle coverage is a mechanical failure.

For strategies requiring active exits, a BUY without paired SELL coverage is a failure.

For hold-to-settlement strategies, explicit settlement tracking is coverage.

## 8. SELL And Exit Rules

Every SELL must be linked to:

- parent entry id
- position id
- hedge plan id
- or explicit strategy exposure action

Duplicate cashout exits for the same entry/token/event must be blocked.

Split exits must record:

- parent BUY
- leg id
- leg type
- shares
- target price
- order type
- fill state
- realized PnL attribution

Late rescue actions must be service-managed and idempotent.

## 9. Budget And PnL

The engine must track separately:

- active cost
- reserved budget
- available strategy budget
- realized PnL
- unrealized mark
- settlement PnL
- account cash balance

Account cash balance must not be inferred from the strategy ledger alone.

## 10. Required Tests

Unit/module tests must cover:

- market BUY intent creation
- limit BUY intent creation
- market SELL intent creation
- limit SELL intent creation
- duplicate intent dedupe
- unfilled order not counted as trade
- partial fill creates partial position
- rejected order preserves reason
- submitted order without reconciliation cannot become position
- missing lifecycle coverage fails
- duplicate cashout is blocked
- parent SELL link is required
- ledger and remote order mismatch triggers reconciliation failure
- restart reloads open orders and positions
