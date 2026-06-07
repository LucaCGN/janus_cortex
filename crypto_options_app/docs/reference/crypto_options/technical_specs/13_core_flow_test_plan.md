# Crypto Options Core Flow Test Plan

Date: 2026-06-03

Status: prepared live-test scope, not yet a performance comparison.

## Purpose

The Core Flow test is the first longer live-structural run after minimal validation. It validates the earliest and most critical application flow:

1. event universe and event-token identity
2. future-event and buying-ahead capture
3. profile/event signal generation
4. candidate generation
5. intent creation
6. supervised executor boundary
7. order/fill reconciliation
8. lifecycle coverage
9. canonical DB persistence
10. `/health` observability

The purpose is still system validation, not strategy performance.

## Strategy Bucket

| Strategy | Role |
| --- | --- |
| `event_context_outcome_v1` | Event-only baseline and target/time/phase validation. |
| `buying_ahead_pre_event_v1` | Future-event capture and pre-start profile buying validation. |
| `s_tier_outcome_consensus_cashout_v1` | Profile outcome consensus and dynamic cashout lifecycle validation. |

## Hard Limits

- Max events: 3
- Hard time limit: 15 minutes / 900 seconds
- Total budget cap: $10
- Sizing: minimal only
- Order submission: supervised runtime only
- Manual orders: prohibited
- Canonical DB persistence: required
- Exchange audit IDs: required
- Health check before and after run: required

## Required Observability During Run

`GET /v1/crypto-options-app/health` must expose:

- canonical DB schema status
- lifecycle table counts
- latest feed watermarks
- strategy registry and prepared Core Flow plan
- latest validation artifact status
- latest order-integrity audit status
- readiness blockers

The run must write:

- `strategy_candidates`
- `execution_intents`
- `orders`
- `fills` for filled orders
- `positions` for filled BUYs
- `exit_plans` for active lifecycle coverage
- `run_reports`

## Start Criteria

The Core Flow run may start only when:

- canonical DB exists and has no missing tables
- Core Flow plan appears in `/health`
- supervised live gates are enabled only for the run process
- executor boundary, ledger gate, risk gate, and reconciliation gate are active
- no duplicate cadence exists
- no current hard safety blocker exists

Historical validation artifacts may remain weakly auditable, but the Core Flow run itself must produce strong audit fields: full token id, event slug, outcome, exchange order id, fill price/size, and reconciliation evidence.

## Stop Criteria

Stop immediately on:

- missing lifecycle coverage
- reconciliation mismatch
- stale critical feed
- duplicate cadence
- exceeded $10 budget cap
- exceeded 3 events
- exceeded 900 seconds
- strategy spec violation
- unexpected traceback

## Prepared Artifact

The prepared run plan is written to:

`local/shared/artifacts/crypto-options-app/reports/core_flow_test_plan_latest.json`
