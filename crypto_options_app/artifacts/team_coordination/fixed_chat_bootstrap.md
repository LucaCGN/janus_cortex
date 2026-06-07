# Fixed Chat Bootstrap

Generated: 2026-06-07T04:15:00Z

## Rule

Do not start fixed chats as independent development lanes until the repo cleanup inventory and issue/milestone plan are current. Fixed chats need clean source-of-truth context before they are useful.

## Signal And Strategy Management Cleanup

Start from:

- `crypto_options_app/artifacts/team_coordination/master_status.md`
- `crypto_options_app/artifacts/team_coordination/promotion_policy.md`
- `crypto_options_app/artifacts/team_coordination/fixed_chat_signal_strategy.md`
- `crypto_options_app/artifacts/team_coordination/github_issue_milestone_plan.md`
- `crypto_options_app/artifacts/reports/transition_readiness_latest.json`

First task:

Review the 90 strategy promotion rows and 235 signal versions. Classify work into retire, revise V2-V5, strict replay required, shadow/live-replay required, or promotion blocked. Do not touch frontend, DB infrastructure, or live execution.

## Frontend Control Center Developer

Start from:

- `crypto_options_app/artifacts/team_coordination/master_status.md`
- `crypto_options_app/artifacts/team_coordination/fixed_chat_frontend.md`
- `crypto_options_app/artifacts/team_coordination/github_issue_milestone_plan.md`
- `crypto_options_app/artifacts/reports/transition_readiness_latest.json`

First task:

Build a frontend contract map and responsive page plan for the full pipeline. Use mock/static contracts for missing endpoints. Do not alter promotion logic, DB infrastructure, or trading runtime.

## Shared Reporting

Each fixed chat must append status to:

- its own coordination file
- `handoff_queue.jsonl` for queue-worthy items
- technical specs only when behavior changes
