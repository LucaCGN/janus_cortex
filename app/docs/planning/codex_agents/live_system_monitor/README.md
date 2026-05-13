# JANUS - Live System Monitor

Purpose: monitor the service-owned live-money operating loop while games are active, using Janus as the authority for StrategyPlanJSON, direct CLOB reconciliation, worker heartbeat, and order submission.

Canonical files:

- `initial_message.md`
- `automation_prompt.md`

Operating stance:

- Live-money mode is expected for the Janus live strategy worker: `dry_run=false` only through the audited strategy-plan execute path.
- The primary live runtime is the service-owned worker exposed at `/v1/ops/live-strategy-worker/*`. Codex must not be the only scheduler during a live game.
- Shadow evaluation must run every tick before any live execution.
- No order is valid unless the current StrategyPlanJSON is present, direct CLOB is funded/flat or reconciled, orderbook and scoreboard gates are fresh, spread is within plan limits, and the plan compiler emits valid intents.
- The quarantined portfolio mirror is never live authority.
- Order sizing comes from operator policy supplied to the live strategy worker/tick path, not from Codex Pregame Research or StrategyPlanJSON sizing metadata.
