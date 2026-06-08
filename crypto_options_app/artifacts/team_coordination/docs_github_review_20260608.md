# Docs And GitHub Source-Of-Truth Review - 2026-06-08

## Purpose

Align active docs and GitHub issues with the current runtime after:

- remote Postgres migration;
- live-loss/reconciliation incident recovery;
- removal of human/Codex approval as a live-promotion mechanic;
- current paired-exit and account-authoritative reconciliation fixes.

## Reviewed Active Docs

- `crypto_options_app/docs/reference/crypto_options/technical_specs/00_current_runtime_source_of_truth.md`
- `crypto_options_app/docs/reference/crypto_options/technical_specs/34_postgres_docker_database_migration_plan.md`
- `crypto_options_app/docs/reference/crypto_options/technical_specs/36_transition_operating_model_2026-06-07.md`
- `crypto_options_app/artifacts/team_coordination/promotion_policy.md`
- `crypto_options_app/artifacts/team_coordination/storage_architecture_decision.md`
- `crypto_options_app/artifacts/team_coordination/master_status.md`
- `crypto_options_app/artifacts/team_coordination/github_issue_milestone_plan.md`
- `crypto_options_app/artifacts/team_coordination/github_source_of_truth_sync.md`
- `crypto_options_app/artifacts/team_coordination/fixed_chat_prompts/signal_strategy_management_cleanup.md`
- `crypto_options_app/artifacts/team_coordination/fixed_chat_frontend.md`
- `crypto_options_app/artifacts/team_coordination/fixed_chat_prompts/frontend_control_center_developer.md`

## Doc Updates Made

- Marked remote Postgres `192.168.0.156:5432/janus-postgres` as the active
  runtime database.
- Marked local Docker Postgres `127.0.0.1:55433` as rollback only.
- Added links to:
  - `postgres_remote_migration_20260608.md`
  - `live_reconciliation_runtime_report_20260608.md`
  - `live_engine_strategy_handoff_20260608.md`
- Replaced active `supervised` wording in fixed-chat prompts and frontend
  prompt docs with app-gated/policy-gated live semantics.
- Preserved legacy `supervised_live` names only as historical/storage
  compatibility labels where needed.
- Documented the current live recovery gate: one fresh controlled paired-exit
  reconciliation cycle before widening live lanes.

## Historical Docs Not Rewritten

Older dated checkpoint docs still contain past wording such as `supervised
live`. Those are historical artifacts and should not override:

- `00_current_runtime_source_of_truth.md`
- `promotion_policy.md`
- runtime policy endpoint `/v1/crypto-options-app/strategies/promotion`
- GitHub issues #151, #152, #168, #172, and #173.

## GitHub Issues Reviewed

Primary current issues:

- #151 Storage architecture audit and Redis hot-plane gate
- #152 Runtime DB adapter audit and SQLite production-path removal
- #153 Promotion/demotion policy rendering and enforcement
- #155 Signal strict replay state cleanup
- #156 Strategy promotion state cleanup
- #159 Shadow/live drift reporting and blocker taxonomy
- #163 Strategy backtest, shadow, live, and promotion views
- #165 Policy-gated live promotion preflight
- #166 Live demotion and stop-gate enforcement
- #167 Budget scaling and descaling policy
- #168 Live reconciliation and shadow/live drift audit
- #171 Strategy pipeline queue starvation/fairness
- #172 Calibrate replay/backtest engine against 2026-06-08 live loss window
- #173 Account-authoritative PnL reconciliation and live loss stops

## GitHub Updates Made

- Renamed milestone `CRYPTO-P7` to
  `CRYPTO-P7 Policy-Gated Live Readiness`.
- Renamed issue #165 to
  `[CRYPTO-P7-01] Policy-gated live promotion preflight`.
- Assigned issues #172 and #173 to the P7 milestone.
- Posted comments:
  - #151 comment `4654408918`: remote Postgres cutover.
  - #152 comment `4654408995`: runtime DB source update and URL parser fix.
  - #168 comment `4654409152`: live reconciliation runtime checkpoint.
  - #172 comment `4654409298`: calibrated replay/backtest status.
  - #173 comment `4654409458`: account-authoritative reconciliation status.

## Current Operating Handoff

Continue from:

`crypto_options_app/artifacts/team_coordination/live_engine_strategy_handoff_20260608.md`

Immediate next validation:

1. Keep remote Postgres active.
2. Run one controlled policy-gated live candidate.
3. Verify BUY fill, paired SELL submission, CLOB reconciliation, local PnL, and
   loss stop behavior.
4. Only then widen live lanes.

## Safety

- No Codex manual orders.
- No gate bypasses.
- Strategy creation/review can define criteria, but app promotion/runtime gates
  decide live progression.
