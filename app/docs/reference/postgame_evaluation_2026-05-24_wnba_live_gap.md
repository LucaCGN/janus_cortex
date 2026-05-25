# Postgame Evaluation - 2026-05-24 WNBA Live Gap

Status: current evaluation
Updated: 2026-05-25

## Scope

This document records why Janus produced no WNBA live trades during the 2026-05-24 live window, despite WNBA StrategyPlans existing for the slate.

Primary local evidence:

- `local/shared/artifacts/live-strategy-worker/2026-05-24/ticks.jsonl`
- `local/shared/artifacts/strategy-plans/2026-05-24/wnba-dal-nyl-2026-05-24/current.json`
- `local/shared/artifacts/strategy-plans/2026-05-24/wnba-phx-atl-2026-05-24/current.json`
- `local/shared/artifacts/strategy-plans/2026-05-24/wnba-wsh-sea-2026-05-24/current.json`
- GitHub `#62`: WNBA controlled min-size live test readiness.

## Result

No WNBA live trades occurred.

The live-strategy worker did evaluate the WNBA events, but every WNBA event ended with:

- event-scoped open positions: `0`
- event-scoped open orders: `0`
- event-scoped direct CLOB trade observations: `0`
- pending intents: `0`
- final blockers: `orderbook_spread_required`

The WNBA live test therefore remains incomplete. `#62` should stay open until one WNBA event has a direct fill-confirmed controlled minimum-size trade, post-call direct CLOB/account reconciliation, and immediate target/stop/review posture.

## Event Outcomes

| Event | Worker ticks | Final score | Final state | Final blocker |
|---|---:|---|---|---|
| `wnba-phx-atl-2026-05-24` | `585` | Phoenix `80`, Atlanta `82` | no positions, no orders, no direct trades | `orderbook_spread_required` |
| `wnba-dal-nyl-2026-05-24` | `585` | Dallas `91`, New York `76` | no positions, no orders, no direct trades | `orderbook_spread_required` |
| `wnba-wsh-sea-2026-05-24` | `585` | Washington `85`, Seattle `97` | no positions, no orders, no direct trades | `orderbook_spread_required` |

## Root Cause

The WNBA plans used the same symmetric `price_stability_micro_grid` shape on both teams:

- `max_spread_cents=2`
- `price_band=[0.03, 0.45]`
- `max_abs_score_gap=18`
- `min_clock_remaining_seconds=60`
- one `5` share limit-buy leg per side
- `target_policy=micro_grid_scaled`

That shape is too brittle for WNBA live-promotion testing. It can be valid for normal spread-compliant grid trades, but it leaves no controlled fallback when WNBA markets are wider, thinner, or outside the generic NBA-derived band. The result was a complete no-trade day even though the issue goal was a controlled minimum-size WNBA live test.

## What Must Change

### 1. Add A WNBA Controlled-Entry Fallback Sleeve

Create a WNBA-specific StrategyPlan family such as `wnba_controlled_min_size_entry_v1`.

It should be separate from ordinary `price_stability_micro_grid` and should:

- allow exactly one minimum-size test leg when ordinary grid spread gates block the whole WNBA event;
- require fresh WNBA scoreboard, period, clock, score, and direct CLOB truth;
- require a direct orderbook snapshot and explicit bid/ask/spread/depth fields in the evidence;
- allow a wider spread cap than NBA grid only for the first controlled live test, for example `<=6c`, if notional is capped;
- choose either best ask for immediate fill proof or a maker price one tick inside the book, depending on configured test mode;
- require immediate post-call direct account/CLOB reconciliation;
- require target/stop/review order posture after fill;
- prohibit duplicate WNBA exposure after one controlled fill until the first lifecycle is reconciled.

### 2. Split WNBA Liquidity Modes

The current plan treats WNBA liquidity as if it should satisfy the same grid rules as NBA. Add explicit modes:

- `grid_mode`: spread-tight, repeated entries allowed only when spread and depth are good.
- `controlled_fill_mode`: one minimum-size trade allowed to prove lifecycle even when grid is not attractive.
- `monitor_only_mode`: no trade when feed, mapping, direct CLOB, or operator gates are not green.

This prevents `orderbook_spread_required` from becoming a silent all-day no-op while preserving safety.

### 3. Make WNBA No-Trade A First-Class Artifact

Every WNBA pass that exits with no trade during an approved live-test window must write a compact blocker artifact with:

- event id;
- score, period, clock, and final status;
- bid, ask, spread, and top depth for both outcomes;
- selected strategy and mode;
- exact blocker;
- whether a controlled fallback was eligible;
- if not eligible, the next patch or config change.

### 4. Keep WNBA Separate From NBA Worker Restarts

During the 2026-05-24 run, the final worker status was NBA-only after the WNBA games had already completed. Future live windows should use either:

- one worker configured for the full NBA/WNBA slate from start through all games, or
- separate service-owned workers/scopes per league.

The important invariant is that stopping/restarting NBA work must not silently end WNBA evaluation or hide WNBA no-trade blockers.

### 5. Acceptance Criteria For Closing `#62`

Do not close `#62` until one WNBA event has:

- current StrategyPlan adopted under canonical WNBA event/market/outcome/token ids;
- fresh WNBA scoreboard and direct CLOB snapshot;
- live preflight ready with no hard blockers;
- one controlled minimum-size order submitted through Janus, not raw exchange;
- post-call direct account/CLOB reconciliation showing fill/open order/position;
- if filled, immediate target/stop/review plan;
- if not filled, explicit cancel/replace/review policy;
- GitHub/repo-doc evidence linking all artifacts.

## Next Implementation Slice

Implement `wnba_controlled_min_size_entry_v1` and tests against the three 2026-05-24 WNBA plans. The first test should prove that when ordinary grid mode blocks on spread, the controlled-fill mode can still produce at most one bounded WNBA order candidate if all non-liquidity gates are green.

