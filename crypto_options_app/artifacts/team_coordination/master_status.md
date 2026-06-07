# Crypto Options Master Status

Updated: 2026-06-07T04:36:20Z

## Objective

Coordinate the crypto-options app transition into a modular operating system for:

`Sources -> Indicators -> Signals -> Signal backtests -> Strategy backtests -> Shadow/live-replay -> Supervised live -> Scale/demote`

The master chat owns high-risk decisions, runtime/storage stability, promotion policy, safety gates, and cross-lane coordination.

## Current State

- Runtime source of truth: Postgres.
- SQLite role: migration/test/compatibility source only until safely archived.
- A/B/C data services: expected to remain fresh before replay expansion.
- Live trading: disabled unless promotion gates pass and supervised runtime is explicitly started.
- Best simple candidate: `profile_splus_hedger_follow_hold_60s_v10 + profile_group_quality`, currently below the 12-sample promotion floor.
- Master hedge-grid track: valid north star, but protected-floor variants replay only when closed-cycle candidates exist.
- Storage audit latest: `postgres_plus_redis_hot_plane_candidate`; Postgres remains durable truth, Redis is disabled and gated.
- Verification latest: storage/Postgres/runtime audit tests passed; health reports Postgres, orders disabled, live false.
- Transition readiness review: `degraded`, broad automation `not_ready`, no accidental live candidates.
- GitHub issue/milestone plan: drafted locally; remote issue creation is gated by repo cleanup inventory and reviewable commits.
- Repo cleanup inventory: path-level artifact generated; 902 dirty/status paths, 219 review-required paths, 681 active crypto paths, 131 crypto compatibility wrapper candidates, 72 legacy move candidates.
- Repo cleanup batches: artifact generated; Batch 0 active crypto baseline branch is `codex/crypto-transition-control-plane`; reference moves and fixed chats remain gated.
- Batch 0 staging plan: 295 stage candidates, 386 hold paths, 0 manual-review paths.
- Batch 0 staging state: 295 source-of-truth candidates staged; generated/data/runtime artifacts remain unstaged.
- Promotion state: 90 strategies, 8 shadow-ready, 0 live candidates, 0 strict signal blockers.

## Active Fixed Chats

- Signal And Strategy Management Cleanup: pending creation from `fixed_chat_signal_strategy.md`.
- Frontend Control Center Developer: pending creation from `fixed_chat_frontend.md`.

## Master Rules

- Do not promote from chat judgment alone.
- Promotion/demotion must come from system evidence and policy.
- No manual orders.
- Global/API live flags stay false.
- Data services, signal validation, strategy backtest, and live-replay remain read-only unless a separate supervised live runtime is explicitly authorized.

## Next Transition Actions

1. Review the staged Batch 0 baseline and commit it separately from reference moves.
2. Keep Batch 0 held runtime/data/generated artifacts unstaged unless explicitly promoted to source-of-truth.
3. Keep Batch 1-4 cleanup on separate branches from `repo_cleanup_batches_latest.json`.
4. Decide which 131 crypto compatibility wrappers must stay until runtime routes are fully cut over.
5. Add DB/runtime adapter tests for remaining production SQLite direct-connect offenders.
6. Add Redis adapter tests for cache/queue TTL before enabling Redis at runtime.
7. Review `SHADOW_READY` rows for recent one-hour economic proof before any live promotion.
8. Create GitHub milestones/issues from `github_issue_milestone_plan.md` after cleanup branches are reviewable.
9. Use `fixed_chat_bootstrap.md` before starting any fixed chat.
