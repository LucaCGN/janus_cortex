# Crypto Options Automation Startup Readiness

- Generated: `2026-06-07T07:09:34.418186+00:00`
- Status: `ready_to_schedule`
- Ready limited automations: `3/3`
- Create immediately: `false`
- Live authority: `none`

## Planned Limited Automations

### `db-data-observability`

- Status: `ready_to_schedule`
- Cadence: `15m`
- Reasoning: `low_or_medium`
- Mode: `report_first`
- Scope: Postgres/runtime, A/B/C/D source health, storage audit, bounded blockers.
- Blockers: `none`
- Required artifacts:
  - `crypto_options_app/artifacts/reports/storage_architecture_audit_latest.json`
  - `crypto_options_app/artifacts/reports/runtime_audit_latest.json`
  - `crypto_options_app/artifacts/reports/transition_readiness_latest.json`

### `signal-strategy-queue-worker`

- Status: `ready_to_schedule`
- Cadence: `5-15m`
- Reasoning: `medium_or_high`
- Mode: `one_bounded_batch`
- Scope: One signal/strategy cleanup row or one bounded batch from the policy-aware cleanup report.
- Blockers: `none`
- Required artifacts:
  - `crypto_options_app/artifacts/reports/signal_strategy_cleanup_batch_latest.md`
  - `crypto_options_app/artifacts/team_coordination/fixed_chat_signal_strategy.md`
  - `crypto_options_app/artifacts/team_coordination/promotion_policy.md`

### `frontend-status-reporter`

- Status: `ready_to_schedule`
- Cadence: `30-60m`
- Reasoning: `low_or_medium`
- Mode: `report_first`
- Scope: Frontend/control-center endpoint status, UI contract drift, and read-only reporting.
- Blockers: `none`
- Required artifacts:
  - `crypto_options_app/artifacts/team_coordination/fixed_chat_frontend.md`
  - `crypto_options_app/artifacts/team_coordination/fixed_chat_prompts/frontend_control_center_developer.md`
  - `crypto_options_app/artifacts/reports/transition_readiness_latest.json`

## Forbidden Until Supervised Live Gate Exists
- `autonomous-live-promotion`
- `broad-multi-lane-worker-swarm`

## Safety

- No automation may authorize live trading.
- No automation may place manual orders.
- No limited automation should be created unless this report remains `ready_to_schedule` and the master/user explicitly requests scheduling.
