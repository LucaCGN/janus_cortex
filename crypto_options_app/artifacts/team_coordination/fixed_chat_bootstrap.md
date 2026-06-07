# Fixed Chat Bootstrap

Generated: 2026-06-07T05:08:00Z

## Rule

Do not start fixed chats as independent development lanes until the repo cleanup inventory and issue/milestone plan are current. Fixed chats need clean source-of-truth context before they are useful.

Prompt root:

`crypto_options_app/artifacts/team_coordination/fixed_chat_prompts/`

Current start gates:

- Frontend Control Center Developer can start after Batch 3 from `fixed_chat_prompts/frontend_control_center_developer.md` if it stays isolated to UI/contracts and does not change backend, DB, promotion, replay, or trading runtime.
- Signal And Strategy Management Cleanup can start from `fixed_chat_prompts/signal_strategy_management_cleanup.md`; GitHub issues #155-#159 are ready.
- Future DB/data, indicator, signal, and strategy specialist prompts live in `fixed_chat_prompts/`, but should not be started as standing fixed chats until the master chat opens those lanes.
- Batch 4 latest artifact: `crypto_options_app/artifacts/reports/compatibility_wrapper_audit_latest.md`.
- Batch 4 decision artifact: `crypto_options_app/artifacts/reports/compatibility_wrapper_decisions_latest.md`.

## Signal And Strategy Management Cleanup

Start from:

- `crypto_options_app/artifacts/team_coordination/master_status.md`
- `crypto_options_app/artifacts/team_coordination/promotion_policy.md`
- `crypto_options_app/artifacts/team_coordination/fixed_chat_signal_strategy.md`
- `crypto_options_app/artifacts/team_coordination/fixed_chat_prompts/signal_strategy_management_cleanup.md`
- `crypto_options_app/artifacts/team_coordination/github_issue_milestone_plan.md`
- `crypto_options_app/artifacts/reports/compatibility_wrapper_audit_latest.json`
- `crypto_options_app/artifacts/reports/transition_readiness_latest.json`

Start gate:

This fixed chat may start now. Batch 4 active imports are cut over, and GitHub issues #155-#159 provide issue-backed queue ownership before touching strategy/signal code and queue semantics.

First task:

Review the 90 strategy promotion rows and 235 signal versions. Classify work into retire, revise V2-V5, strict replay required, shadow/live-replay required, or promotion blocked. Do not touch frontend, DB infrastructure, or live execution.

## Frontend Control Center Developer

Start from:

- `crypto_options_app/artifacts/team_coordination/master_status.md`
- `crypto_options_app/artifacts/team_coordination/fixed_chat_frontend.md`
- `crypto_options_app/artifacts/team_coordination/fixed_chat_prompts/frontend_control_center_developer.md`
- `crypto_options_app/artifacts/team_coordination/github_issue_milestone_plan.md`
- `crypto_options_app/artifacts/reports/transition_readiness_latest.json`

Start gate:

Can start now after Batch 3, provided the lane uses API contracts/mocks for missing endpoints and does not change backend, DB, promotion, replay, or trading runtime.

First task:

Build a frontend contract map and responsive page plan for the full pipeline. Use mock/static contracts for missing endpoints. Do not alter promotion logic, DB infrastructure, or trading runtime.

## Shared Reporting

Each fixed chat must append status to:

- its own coordination file
- `handoff_queue.jsonl` for queue-worthy items
- technical specs only when behavior changes
