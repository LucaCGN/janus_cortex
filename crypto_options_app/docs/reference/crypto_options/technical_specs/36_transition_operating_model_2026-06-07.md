# Crypto Options Transition Operating Model - 2026-06-07

## Summary

The crypto-options app is transitioning from ad hoc strategy-chasing into a modular operating system for research, replay, shadow testing, supervised live promotion, and demotion.

The target flow is:

`Sources -> Indicators -> Signals -> Signal backtests -> Strategy backtests -> Shadow/live-replay -> Supervised live -> Scale/demote`

Current readiness is not sufficient for broad autonomous live promotion. The app can support bounded queue cleanup, source monitoring, and frontend reporting while the master chat finishes storage, promotion, and repo-organization gates.

## Current Controller Model

The master chat owns:

- runtime and storage decisions
- live-promotion policy
- safety gates
- end-to-end promotion/demotion integrity
- coordination across fixed chats and automations

The first two fixed chats are:

- Signal And Strategy Management Cleanup
- Frontend Control Center Developer

Both consume and write coordination state under:

`crypto_options_app/artifacts/team_coordination`

## Storage Decision Gate

Postgres remains durable source of truth.

Redis is a gated hot-plane candidate, not a replacement for Postgres. It may be introduced only for latest-state cache, frontend cache, replay candidate cache, TTL queue locks, pub/sub, rate limits, or short-lived coordination after the storage audit shows pressure that belongs outside durable historical storage.

Redis must never be the only store for trading-critical truth.

The audit command is:

```powershell
python -m crypto_options_app.scripts.run_crypto_options_storage_architecture_audit --write-artifacts --json
```

## Runtime DB Rules

- Runtime API, dashboard, strategy manager, signal manager, replay, and data services must use the common DB adapter.
- SQLite remains allowed only for migration, import, test, or compatibility paths until the large compatibility file can be safely archived.
- Runtime audits must fail production paths that call `sqlite3.connect()` directly.
- Replay/scout queries must stay bounded and resource-aware.

## Promotion And Demotion Rules

Signal `PASSED`, `SELECTED`, or `STRUCTURAL_ALTERNATE` states are not live-safe by themselves.

Strategy live promotion requires:

- 12+ recent distinct economic samples
- win rate above 70%
- positive simulated PnL
- lifecycle coverage
- reconciliation passed
- no strict signal blockers
- shadow/live-replay drift within tolerance

Live loss, reconciliation mismatch, missing lifecycle, large drift, negative PnL breach, or win-rate breach demotes or blocks according to strategy policy.

## Strategy Tracks

### Simple Candidate Track

Continue accumulating `profile_splus_hedger_follow_hold_60s_v10 + profile_group_quality` evidence. It must not promote until it reaches the minimum distinct-sample gate and passes promotion manager reconciliation.

### Master Hedge Grid Track

Continue `master_hedge_grid_scalping` as volatility harvesting:

- seed both sides
- scalp inversions/rebounds
- lock a protected hedge floor
- allow only floor-preserving orders
- spend only surplus on 1c/5c/10c tail optionality

Replay only when closed-cycle protected-floor candidates exist. Do not weaken floor gates to force execution.

## Repo Organization

Do not delete old work.

Planned reference roots:

- `wnba_nba_app_reference`
- `global_app_reference`

Crypto active roots:

- `crypto_options_app`
- `tests/crypto_options_app`
- crypto-specific scripts, specs, artifacts, issues, and workflows

Move legacy work only after an inventory report.

## Automation Gate

Keep the single heartbeat/master automation active.

Do not add broad standing automations until:

- coordination artifacts exist
- storage audit completes
- promotion/demotion tests are in place
- queue ownership is verified
- fixed chats are created or explicitly deferred

Allowed next automations after gates:

- DB/data observability
- signal-strategy queue worker
- frontend status reporter

No autonomous live-promotion automation is allowed until supervised live promotion policy has passing tests and user approval.
