# Fixed Chat Prompt: Signal And Strategy Management Cleanup

## Start Gate

Ready. Batch 4 active imports are cut over, compatibility wrapper decisions are reviewable, and GitHub issues #155-#159 exist.

This lane touches signal/strategy code and queue semantics, so it must work from GitHub issues and team coordination artifacts before broad changes.

## Role

You own signal and strategy cleanup for the Crypto Options App.

Repo: `C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex`
App root: `crypto_options_app`
Coordination root: `crypto_options_app/artifacts/team_coordination`

## Read First

- `crypto_options_app/artifacts/team_coordination/master_status.md`
- `crypto_options_app/artifacts/team_coordination/promotion_policy.md`
- `crypto_options_app/artifacts/team_coordination/handoff_queue.jsonl`
- `crypto_options_app/artifacts/team_coordination/github_issue_milestone_plan.md`
- `crypto_options_app/artifacts/team_coordination/github_source_of_truth_sync.md`
- `crypto_options_app/artifacts/reports/compatibility_wrapper_audit_latest.json`
- `crypto_options_app/artifacts/reports/transition_readiness_latest.json`

## Scope

Work on:

- signal review, retirement, V2-V5 variants, strict replay interpretation
- strategy review, simple strategy candidates, queue hygiene, shadow/live-replay evidence
- blocker/next-action messages that are useful to Codex and visible in the frontend

Do not work on:

- DB infrastructure or storage architecture
- frontend styling/layout
- trading runtime or live child processes
- manual orders

## Operating Rules

1. Claim one bounded row or batch in `handoff_queue.jsonl` when possible.
2. Prefer retiring weak stale variants over keeping ambiguous review rows.
3. Create V2-V5 only when the variant covers a real gap or fixes a concrete blocker.
4. Do not treat `PASSED`, `SELECTED`, or `STRUCTURAL_ALTERNATE` as live-safe without strict replay and strategy evidence.
5. Never promote live from chat judgment.

## Strategy Direction

Maintain two tracks:

- Simple profile-follow proof path: `profile_splus_hedger_follow_hold_60s_v10 + profile_group_quality`.
- Master hedge-grid path: volatility harvesting, protected floor, floor-preserving orders, surplus tail optionality.

## First Task

Review current strategy/signal tables and classify rows into:

`PROMOTED`, `REVIEW`, `RETIRED`, `BLOCKED`, `NEEDS_VARIANT`, `STRICT_REPLAY_REQUIRED`, or `SHADOW_REQUIRED`.
