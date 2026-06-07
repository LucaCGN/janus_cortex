# Crypto Options Automation Registry

Updated: 2026-06-07T03:35:00Z

## Active

- `crypto-options-unified-dev-loop`: master heartbeat/controller. Performs bounded manager slices only.

## Planned, Not Yet Active

- `db-data-observability`: 15 minute cadence, low/medium reasoning, reports source/storage health and bounded blockers.
- `signal-strategy-queue-worker`: 5-15 minute cadence, medium/high reasoning, handles one signal/strategy queue item or bounded batch.
- `frontend-status-reporter`: 30-60 minute cadence, low/medium reasoning, report-focused.

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
