# Crypto Options Automation Registry

Updated: 2026-06-07T07:45:00Z

## Active

- `crypto-options-db-data-observability`: active cron, 15 minute cadence, report-first DB/data/source health lane. Canonical backend checks use `http://127.0.0.1:8011/v1/crypto-options-app`.
- `crypto-options-signal-strategy-queue-worker`: active cron, 15 minute cadence, one bounded signal/strategy cleanup queue item or proposal.
- `crypto-options-frontend-status-reporter`: active cron, hourly cadence, report-only frontend/control-center status lane.

Required latest report paths:

- `crypto-options-db-data-observability`: `crypto_options_app/artifacts/team_coordination/automation_status/db_data_observability_latest.md`
- `crypto-options-signal-strategy-queue-worker`: `crypto_options_app/artifacts/team_coordination/automation_status/signal_strategy_queue_worker_latest.md`
- `crypto-options-frontend-status-reporter`: `crypto_options_app/artifacts/team_coordination/automation_status/frontend_status_reporter_latest.md`

## Master Controller

- Current master control remains this active chat goal/thread.
- Existing app heartbeat `crypto-options-unified-dev-loop` is present but currently `PAUSED` in the Codex automation store. Do not duplicate it; reactivate/update only if the user explicitly asks for the separate heartbeat to resume.

## Planned, Not Yet Active

- No additional standing automations should be created until these three limited lanes prove useful through markdown/JSON reports.

Latest machine-readable gate:

- `crypto_options_app/artifacts/reports/automation_startup_readiness_latest.md`
- Status: `ready_to_schedule`
- Ready limited automations: `3/3`
- Create immediately: `false`
- Activated after master-goal continuation: `3/3` limited automations.

Latest report freshness gate:

- `crypto_options_app/artifacts/reports/automation_report_status_latest.md`
- Current status: `fresh`; DB/data observability, signal/strategy queue worker, and frontend status reporter all have required latest reports.

## Not Allowed Yet

- Autonomous live-promotion automation.
- Broad multi-lane worker swarm without durable queue ownership.
- Any automation that silently mutates state without writing markdown/JSON status.

## Activation Gate

Do not activate new standing automations until:

- `promotion_policy.md` is implemented in tests.
- storage audit completes.
- queue ownership contract is verified.
- fixed chats are created or explicitly deferred.
- `python -m crypto_options_app.scripts.run_crypto_options_automation_startup_readiness --write-artifacts --markdown` reports `ready_to_schedule`.

Even when the gate is ready, scheduling still requires a master/user request. This registry does not authorize autonomous live promotion or broad worker swarms.

## Active Safety Contract

- No active automation may authorize live trading.
- No active automation may place manual orders.
- No active automation may alter global/API live flags.
- Signal/strategy cleanup automation treats `PROMOTED` as cleanup classification only, never live authority.
- Frontend automation is report-only and must not change DB, promotion, replay, strategy, or trading runtime logic.
- Missing or stale reports block adding further standing automations.
