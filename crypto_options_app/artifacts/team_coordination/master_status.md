# Crypto Options Master Status

Updated: 2026-06-07T07:31:00Z

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
- Storage audit latest: `postgres_plus_redis_hot_plane_candidate`; Postgres remains durable truth, Redis is disabled and gated behind tested TTL cache/lock adapter semantics.
- Verification latest: storage/Postgres/runtime audit tests passed; health reports Postgres, orders disabled, live false.
- Runtime SQLite audit latest: 0 production runtime direct-connect blockers, 0 review-required SQLite usages, 11 allowed migration/test/compat usages; safe audit artifact at `crypto_options_app/artifacts/reports/runtime_audit_latest.md`.
- Transition readiness review: `degraded`, broad automation `not_ready`, no blockers, no accidental live candidates. The live `/strategies/promotion` endpoint exposes `crypto_options_promotion_policy_contract_v1`.
- GitHub issue/milestone source-of-truth: created in `LucaCGN/janus_cortex`; sync report at `crypto_options_app/artifacts/team_coordination/github_source_of_truth_sync.md`.
- Review branch: draft PR [#170 Crypto options compatibility wrapper cutover](https://github.com/LucaCGN/janus_cortex/pull/170).
- Repo cleanup inventory: path-level artifact generated; 534 dirty/status paths, 132 review-required paths, 402 active crypto paths, 131 crypto compatibility wrapper candidates, 0 legacy move candidates.
- Repo cleanup batches: artifact generated; Batch 0 active crypto baseline branch is `codex/crypto-transition-control-plane`; Batch 1 local/root branch is `codex/crypto-repo-local-state-cleanup`; Batch 2 WNBA/NBA branch is `codex/crypto-repo-wnba-nba-reference`; Batch 3 global reference branch is `codex/crypto-repo-global-reference`; Batch 4 compatibility review branch is `codex/crypto-compatibility-wrapper-cutover`.
- Batch 0 staging plan: 295 stage candidates, 386 hold paths, 0 manual-review paths.
- Batch 0 baseline commit: `d642702` (`Add crypto options transition control plane baseline`) with 295 source-of-truth paths. Generated/data/runtime artifacts remain unstaged.
- Batch 0 handoff commit: `b995271` (`Record crypto baseline handoff`).
- Batch 1 local/root review commit: `645eb99` (`Ignore local Codex automation memory`); `.codex_automation_memory/` is ignored, and the `streamlit` dependency change remains held until the frontend/tooling path decides whether the observer app stays active.
- Batch 2 WNBA/NBA reference move commit: `c67705e` (`Move WNBA NBA docs into reference root`).
- Batch 3 global reference move commit: `659a722` (`Move global legacy files into reference root`).
- Batch 4 compatibility wrapper audit: `crypto_options_app/artifacts/reports/compatibility_wrapper_audit_latest.md`; 131 candidates, 0 referenced by active crypto code/tests, 52 docs/reference-only wrappers, 59 no-reference wrappers, automatic wrapper moves still blocked until non-active wrapper decisions are reviewed.
- Batch 4 compatibility wrapper decision plan: `crypto_options_app/artifacts/reports/compatibility_wrapper_decisions_latest.md`; active import blockers cleared, non-active wrapper decisions reviewable, GitHub issue creation can start after this branch is reviewable.
- Batch 4 cutover progress: active crypto callers now use `crypto_options_app.data_nodes.polymarket_crypto`, `crypto_options_app.data_nodes.crypto`, `crypto_options_app.pipelines.options`, `crypto_options_app.services.crypto_options`, `crypto_options_app.runtime.local_paths`, `crypto_options_app.api.db`, and `crypto_options_app.trading.polymarket_portfolio` instead of old `app.*` runtime imports.
- Fixed chat prompt folder: `crypto_options_app/artifacts/team_coordination/fixed_chat_prompts/`. Frontend fixed chat can start from `fixed_chat_prompts/frontend_control_center_developer.md`; signal/strategy cleanup can start from `fixed_chat_prompts/signal_strategy_management_cleanup.md`.
- Promotion state: 110 strategies, 12 shadow-ready, 0 live candidates, 0 strict signal blockers.
- Promotion policy contract: strategy promotion summaries expose `crypto_options_promotion_policy_contract_v1`; `PASSED`/`SELECTED`/`STRUCTURAL_PASS`/`STRUCTURAL_ALTERNATE` are not promotable signal states, exactly 70% win rate is not enough for live candidacy, and chat/automation cannot authorize live orders.
- Transition readiness now blocks if the strategy promotion endpoint lacks `crypto_options_promotion_policy_contract_v1`.
- Frontend strategy lab now renders the `policy_contract` from `/strategies/promotion`, including live-candidate gates, the `PROMOTION_READY` signal gate, non-promotable signal labels, strict blocker allowance, and supervised-runtime-only live authority.
- Strategy revision scout now consumes `crypto_options_promotion_policy_contract_v1`; it reports non-promotable signal labels by state/type/source and uses policy-derived live-candidate thresholds instead of hard-coded queue cleanup assumptions.
- Signal/strategy cleanup batch CLI is available: `python -m crypto_options_app.scripts.run_crypto_options_signal_strategy_cleanup_batch --max-signals 24 --max-strategies 12`. It is read-only and emits bounded cleanup classifications for fixed chats and future queue workers.
- First bounded cleanup batch artifact: `crypto_options_app/artifacts/reports/signal_strategy_cleanup_batch_latest.md`, generated at `2026-06-07T06:56:17Z` with 12 signal rows and 6 strategy rows. Overall queue shape: 235 signals (`125 NEEDS_VARIANT`, `97 PROMOTED` cleanup classification only, `13 STRICT_REPLAY_REQUIRED`) and 90 strategies (`1 BLOCKED`, `54 NEEDS_VARIANT`, `35 SHADOW_REQUIRED`). No live authority is implied.
- Fixed chat startup readiness artifact: `crypto_options_app/artifacts/reports/fixed_chat_startup_readiness_latest.md`; status `ready`, `2/2` current fixed chats ready, future specialist prompts remain future-only, and no fixed chat has live authority.
- Limited automation startup readiness artifact: `crypto_options_app/artifacts/reports/automation_startup_readiness_latest.md`; status `ready_to_schedule`, `3/3` planned limited automations ready, `create_immediately=false`, no live authority.
- Limited automations activated: `crypto-options-db-data-observability` every 15 minutes, `crypto-options-signal-strategy-queue-worker` every 15 minutes, and `crypto-options-frontend-status-reporter` hourly. Existing `crypto-options-unified-dev-loop` heartbeat remains paused in the app automation store; current master control is this active chat goal/thread.
- Limited automation report freshness artifact: `crypto_options_app/artifacts/reports/automation_report_status_latest.md`; current status `degraded`. The signal/strategy queue worker has a fresh first report; DB/data observability and frontend status reporter are still pending first reports.

## Active Fixed Chats

- Signal And Strategy Management Cleanup: eligible to start from `fixed_chat_prompts/signal_strategy_management_cleanup.md`; primary GitHub issues #155-#159.
- Frontend Control Center Developer: eligible to start from `fixed_chat_prompts/frontend_control_center_developer.md`; primary GitHub issues #160-#164; must avoid backend, DB, promotion, and trading runtime changes.

## Master Rules

- Do not promote from chat judgment alone.
- Promotion/demotion must come from system evidence and policy.
- No manual orders.
- Global/API live flags stay false.
- Data services, signal validation, strategy backtest, and live-replay remain read-only unless a separate supervised live runtime is explicitly authorized.

## Next Transition Actions

1. Commit the Batch 4 decision plan so non-active wrapper handling is reviewable.
2. Start fixed chats from their prompt files and linked GitHub issues when user is ready; verify `fixed_chat_startup_readiness_latest.md` remains `ready` and `transition_readiness_latest.md` has no blockers first.
3. Monitor `automation_report_status_latest.md`; do not add or reactivate any other standing automation until DB/data, signal/strategy, and frontend limited lanes all produce fresh reports.
4. Keep Batch 0 held runtime/data/generated artifacts unstaged unless explicitly promoted to source-of-truth.
5. Keep legacy SQLite profile/market side stores fenced to old research CLIs only; their defaults now point to `local/shared/artifacts/crypto-options-research/...`, not the central runtime DB path.
6. Keep Redis disabled until a measured hot-plane use case is selected; use only TTL cache/queue-lock semantics through the adapter, never durable trading truth.
7. Let the Signal/Strategy Management Cleanup fixed chat start from `signal_strategy_cleanup_batch_latest.md` and GitHub issues #155-#159; first work should retire or create justified V2-V5 variants for the first weak hedge-grid signal rows and revise/retire the first six stale strategy rows.
8. Continue issue #153 by wiring the same cleanup classifications into a bounded queue-worker automation after the fixed-chat workflow proves the handoff path.
9. Use `fixed_chat_bootstrap.md` and `fixed_chat_prompts/README.md` before starting any fixed chat.
