# Crypto Options GitHub Issue And Milestone Plan

Generated: 2026-06-07T04:15:00Z

## Purpose

Define the issue/milestone structure required before fixed chats and automations can scale safely. Do not create broad GitHub issues until the repo cleanup inventory is reviewed and the worktree can produce meaningful crypto-focused commits.

## Milestones

### CRYPTO-P4 Transition Control Plane

Goal: make the app safe for modular fixed chats and bounded automations.

Issues:

1. `[CRYPTO-P4-01] Team coordination artifacts and fixed-chat handoff contracts`
2. `[CRYPTO-P4-02] Storage architecture audit and Redis hot-plane gate`
3. `[CRYPTO-P4-03] Runtime DB adapter audit and SQLite production-path removal`
4. `[CRYPTO-P4-04] Promotion/demotion policy rendering and enforcement`
5. `[CRYPTO-P4-05] Repo cleanup inventory and reference-root move plan`

### CRYPTO-P5 Signal And Strategy Queue Trust

Goal: make signal/strategy review and queue progression reliable enough for fixed chats.

Issues:

1. `[CRYPTO-P5-01] Signal strict replay state cleanup`
2. `[CRYPTO-P5-02] Strategy promotion state cleanup`
3. `[CRYPTO-P5-03] Profile-follow candidate evidence accumulation`
4. `[CRYPTO-P5-04] Hedge-grid protected-floor selector readiness`
5. `[CRYPTO-P5-05] Shadow/live drift reporting and blocker taxonomy`

### CRYPTO-P6 Frontend Control Center

Goal: make the frontend the working control center for the full pipeline.

Issues:

1. `[CRYPTO-P6-01] Root operating dashboard flow`
2. `[CRYPTO-P6-02] Source and indicator health views`
3. `[CRYPTO-P6-03] Signal backtest and live-shadow views`
4. `[CRYPTO-P6-04] Strategy backtest, shadow, live, and promotion views`
5. `[CRYPTO-P6-05] Portfolio, positions, open orders, history, event, and profile views`

### CRYPTO-P7 Supervised Live Readiness

Goal: promote only reconciled candidates that pass policy.

Issues:

1. `[CRYPTO-P7-01] Supervised live promotion preflight`
2. `[CRYPTO-P7-02] Live demotion and stop-gate enforcement`
3. `[CRYPTO-P7-03] Budget scaling and descaling policy`
4. `[CRYPTO-P7-04] Live reconciliation and shadow/live drift audit`
5. `[CRYPTO-P7-05] First live candidate readiness report`

## Labels

- `crypto-options`
- `transition`
- `db-data`
- `signals`
- `strategies`
- `frontend`
- `promotion-policy`
- `safety`
- `repo-cleanup`
- `blocked`
- `ready-for-fixed-chat`

## Creation Gate

Create these issues only after:

- `transition_readiness_latest.json` exists.
- `repo_cleanup_inventory.md` is current.
- direct runtime SQLite offender report is reviewed.
- the worktree can be split into reviewable crypto-focused commits.
