# Live Engine, Replay, And Strategy Creation Handoff - 2026-06-08

## Current Runtime

- App UI: `http://127.0.0.1:8011/v1/crypto-options-app`
- Health: `http://127.0.0.1:8011/v1/crypto-options-app/health`
- Runtime DB: remote Postgres
  - Host: `192.168.0.156`
  - Port: `5432`
  - Database: `janus-postgres`
  - Env: `JANUS_CRYPTO_OPTIONS_POSTGRES_URL`
- Local Docker Postgres on `127.0.0.1:55433` remains rollback only.
- Latest DB migration artifact:
  `crypto_options_app/artifacts/team_coordination/postgres_remote_migration_20260608.md`

## Live/Reconciliation State

Latest runtime report:

`crypto_options_app/artifacts/team_coordination/live_reconciliation_runtime_report_20260608.md`

Current state after the last reconciliation pass:

- Active live orders: `0`
- Active live positions: `0`
- Remaining live candidates: `3`
  - `master_hedge_grid_floor_paired_seed_builder_v5`
  - `master_hedge_grid_floor_paired_seed_builder_v6`
  - `profile_splus_outcome_prediction_confluence_v1`
- All three refused to trade because their own strategy-defined gates rejected
  current market/profile conditions.
- No Codex manual orders were placed.

## Verified Runtime Fixes

- Paired sell pricing no longer uses the broken same-price or `0.99` fallback.
- BUY fills that require paired exits now need executable SELL coverage.
- Underpriced paired exits are cancelled or blocked by reconciliation.
- Pending/stale exchange-backed local orders are reconciled against CLOB
  open-order/order/trade state and expired locally when no live evidence exists.
- Cumulative live loss demotion triggers at `<=` the strategy-owned loss
  threshold. Current default compatibility policy uses `10.0` USD unless a
  stricter strategy value is set.
- Promotion-manager tests pass under live-runtime env flags.

## Open Validation Gate

Before widening live lanes:

1. Run one controlled policy-gated live candidate.
2. Confirm entry BUY is filled.
3. Confirm paired SELL is immediately submitted when the strategy requires it.
4. Confirm the paired SELL reconciles through exchange state.
5. Confirm PnL/loss state updates from account-authoritative evidence.
6. Confirm stop/demotion fires if realized PnL hits the strategy-owned loss
   threshold.

Only after this clean cycle should the app widen toward three simultaneous live
lanes again.

## Strategy Work Rules

Codex/fixed chats should create and review strategies/signals only. They should
not decide live entry by chat judgment and should not place manual orders.

Every strategy must encode:

- historical backtest criteria
- shadow/live-replay criteria
- live entry criteria
- budget cap and order sizing
- max realized live PnL loss
- loss-streak demotion
- lifecycle/reconciliation requirements
- drift limits
- scale/descale rules

The app promotion manager and queue worker own progression:

`registered -> historical_backtest -> shadow_replay -> recent_shadow_sample -> LIVE_CANDIDATE -> live -> scale/demote/review`

## Next Strategy Creation Priorities

1. Continue conservative profile/outcome prediction candidates only if they pass
   calibrated replay and paired-exit lifecycle assumptions.
2. Resume `master_hedge_grid_scalping` only with closed-cycle protected-floor
   candidates; do not weaken floor gates to force execution.
3. Add or review signals for:
   - inversion count prediction
   - zero-inversion prediction
   - rebound count prediction
   - zero-rebound prediction
   - swing-band prediction
4. Any new grid/hedge strategy that buys both sides must define grid spacing,
   minimum order/share size, paired exit behavior, and per-event exposure
   before it can be live-capable.

## Replay/Trading Engine Follow-Up

Use issues:

- #172: calibrated replay/backtest engine against the 2026-06-08 live-loss
  window.
- #173: account-authoritative PnL reconciliation and live loss stops.
- #168: broader live reconciliation and shadow/live drift audit.

Required next evidence:

- Backtest the same time window as the live incident and compare expected vs
  actual fills/PnL.
- Keep latency/slippage as one measured factor, not the only explanation.
- Treat quote/path data quality as a blocker when the replay engine cannot
  model the live path honestly.
- Validate losing `$0.00` Polymarket positions and auto-redeemed winners both
  map to local PnL and stop gates.

## GitHub Source Of Truth

Updated on 2026-06-08:

- Milestone `CRYPTO-P7` renamed to `CRYPTO-P7 Policy-Gated Live Readiness`.
- Issue #165 renamed to policy-gated live promotion preflight.
- Issues #172 and #173 assigned to the P7 milestone.
- Comments posted:
  - #151 remote Postgres cutover
  - #152 runtime DB source update
  - #168 live reconciliation runtime checkpoint
  - #172 replay/backtest calibration status
  - #173 account-authoritative reconciliation status

## Do Not Do

- Do not widen live lanes before one clean paired-exit reconciliation cycle.
- Do not treat historical positive backtests as sufficient if calibrated replay
  marks quote/path quality unsafe.
- Do not let stale local `submitted` orders create false open exposure.
- Do not place Codex manual orders.
