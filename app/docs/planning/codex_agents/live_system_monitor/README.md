# JANUS - Live System Monitor

Purpose: run the live-money operating loop while games are active, using Janus as the authority for StrategyPlanJSON, direct CLOB reconciliation, and order submission.

Canonical files:

- `initial_message.md`
- `automation_prompt.md`

Operating stance:

- Live-money mode is expected for the live monitor: `dry_run=false` only through the audited strategy-plan execute path.
- Shadow evaluation must run every tick before any live execution.
- No order is valid unless the current StrategyPlanJSON is present, direct CLOB is funded/flat or reconciled, orderbook and scoreboard gates are fresh, spread is within plan limits, and the plan compiler emits valid intents.
- The quarantined portfolio mirror is never live authority.
- Order sizing comes from operator policy supplied to `run_live_strategy_tick.py`, not from Codex Pregame Research or StrategyPlanJSON sizing metadata.
