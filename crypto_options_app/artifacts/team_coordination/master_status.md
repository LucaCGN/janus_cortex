# Crypto Options Master Status

Updated: 2026-06-07T05:08:00Z

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
- Repo cleanup inventory: path-level artifact generated; 534 dirty/status paths, 132 review-required paths, 402 active crypto paths, 131 crypto compatibility wrapper candidates, 0 legacy move candidates.
- Repo cleanup batches: artifact generated; Batch 0 active crypto baseline branch is `codex/crypto-transition-control-plane`; Batch 1 local/root branch is `codex/crypto-repo-local-state-cleanup`; Batch 2 WNBA/NBA branch is `codex/crypto-repo-wnba-nba-reference`; Batch 3 global reference branch is `codex/crypto-repo-global-reference`; Batch 4 compatibility review branch is `codex/crypto-compatibility-wrapper-cutover`.
- Batch 0 staging plan: 295 stage candidates, 386 hold paths, 0 manual-review paths.
- Batch 0 baseline commit: `d642702` (`Add crypto options transition control plane baseline`) with 295 source-of-truth paths. Generated/data/runtime artifacts remain unstaged.
- Batch 0 handoff commit: `b995271` (`Record crypto baseline handoff`).
- Batch 1 local/root review commit: `645eb99` (`Ignore local Codex automation memory`); `.codex_automation_memory/` is ignored, and the `streamlit` dependency change remains held until the frontend/tooling path decides whether the observer app stays active.
- Batch 2 WNBA/NBA reference move commit: `c67705e` (`Move WNBA NBA docs into reference root`).
- Batch 3 global reference move commit: `659a722` (`Move global legacy files into reference root`).
- Batch 4 compatibility wrapper audit: `crypto_options_app/artifacts/reports/compatibility_wrapper_audit_latest.md`; 131 candidates, 53 referenced by active crypto code/tests, 55 no-reference wrappers, automatic wrapper moves blocked.
- Fixed chat prompt folder: `crypto_options_app/artifacts/team_coordination/`. Frontend fixed chat can start after Batch 3 from `fixed_chat_frontend.md`; signal/strategy cleanup should wait for Batch 4 wrapper decisions plus GitHub milestones/issues.
- Promotion state: 90 strategies, 8 shadow-ready, 0 live candidates, 0 strict signal blockers.

## Active Fixed Chats

- Signal And Strategy Management Cleanup: pending creation from `fixed_chat_signal_strategy.md`; gated by Batch 4 compatibility decisions and GitHub source-of-truth.
- Frontend Control Center Developer: eligible to start from `fixed_chat_frontend.md` after Batch 3; must avoid backend, DB, promotion, and trading runtime changes.

## Master Rules

- Do not promote from chat judgment alone.
- Promotion/demotion must come from system evidence and policy.
- No manual orders.
- Global/API live flags stay false.
- Data services, signal validation, strategy backtest, and live-replay remain read-only unless a separate supervised live runtime is explicitly authorized.

## Next Transition Actions

1. Cut active crypto callers away from the 53 referenced old `app.*`/`codex_tool.*` compatibility wrappers in small tested groups.
2. Review the 55 no-reference wrappers for compatibility archive or removal after active tests prove they are unused.
3. Keep Batch 0 held runtime/data/generated artifacts unstaged unless explicitly promoted to source-of-truth.
4. Add DB/runtime adapter tests for remaining production SQLite direct-connect offenders.
5. Add Redis adapter tests for cache/queue TTL before enabling Redis at runtime.
6. Create GitHub milestones/issues from `github_issue_milestone_plan.md` after Batch 4 decisions are reviewable.
7. Use `fixed_chat_bootstrap.md` before starting any fixed chat.
