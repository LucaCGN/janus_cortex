# Crypto Options Trading Engine Design

Date: 2026-06-02

Status: technical spec for execution lifecycle and reconciliation.

## Purpose

The trading engine converts validated strategy candidates into supervised execution intents and reconciled trading state.

It must never assume that submission equals fill or that fill equals settled PnL.

## Lifecycle Objects

| Object | Meaning |
| --- | --- |
| Candidate | Strategy-observed opportunity before execution checks. |
| Intent | Idempotent request to attempt a specific order action. |
| Order | Submitted executor request and exchange-facing metadata. |
| Fill | Reconciled filled or partially filled quantity. |
| Position | Open exposure created by fills. |
| Exit plan | Explicit lifecycle coverage for a position. |
| Exit order | SELL/cashout order linked to a position or hedge action. |
| Settlement | Final event outcome and realized value. |
| PnL | Realized, unrealized, active cost, and budget impact. |

## Supported Order Types

- market BUY
- market SELL
- limit BUY
- limit SELL

Each strategy declares allowed order types. Market/limit conversion is forbidden unless the strategy explicitly allows it.

## Idempotency

Every candidate, intent, order, fill, exit, and settlement row must have a stable idempotency key.

Intent keys include:

- strategy id
- run id
- event identity
- token id
- outcome
- side
- intent type
- source attribution
- decision timestamp bucket
- target size/price

Duplicate submissions for the same intent are mechanical failures unless marked as a retry of the same idempotency key.

## Reconciliation

Reconciliation compares:

- local order state
- remote Polymarket CLOB/order state
- local filled size
- remote filled size
- open exposure
- SELL/cashout state
- candidate ledger
- raw executor/supervisor artifacts

Submitted orders without reconciliation do not create positions.

## Lifecycle Coverage

Every active BUY fill must have explicit coverage:

- active cashout order
- split exit plan
- hedge-managed inventory plan
- hold-to-settlement policy
- late rescue policy
- reconciled settlement state

Missing coverage is a mechanical failure.

## Restart Behavior

On restart, the engine reloads:

- active run config
- open intents
- submitted orders
- fills
- positions
- exit plans
- stop gate state
- exposure snapshots

Restart must not create duplicate cadence or duplicate intents.

## Acceptance Criteria

- Candidate, intent, order, fill, position, exit, settlement, and PnL are separate.
- Partial fills and unfilled orders are handled correctly.
- Every BUY path has lifecycle coverage.
- Reconciliation is required before local state becomes authoritative.

## P2 Centralization Update

Managed strategy runtime must cover cashout SELL intents, rebuy targets after profitable cashout, buy-only hedge-ratio rebalance, and stale-order review every 30-60 seconds. These mechanics emit intent plans only; supervised executor gates still own order submission.
