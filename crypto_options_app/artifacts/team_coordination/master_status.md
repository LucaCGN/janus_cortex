# Crypto Options Master Status

Updated: 2026-06-08T13:03:00Z

## Objective

Coordinate the crypto-options app transition into a modular operating system for:

`Sources -> Indicators -> Signals -> Signal backtests -> Strategy backtests -> Shadow/live-replay -> Live -> Scale/demote`

The master chat owns runtime/storage stability, promotion-policy implementation, safety gates, and cross-lane coordination. It does not approve individual strategy promotion, demotion, scaling, or live entry by judgment; the app state machine applies strategy-defined criteria.

## Current State

- Runtime source of truth: Postgres.
- SQLite role: migration/test/compatibility source only until safely archived.
- A/B/C/D data services: A Crypto, B Profiles, C Options, and D market activity are fresh on the 8011 health endpoint. C Options is fresh for BTC/ETH after fixing bounded target selection so `--max-tokens 8` no longer starves ETH. B Profiles loop is running with bounded external profile fetch. D `polymarket_live_activity_capture` is running as a bounded read-only loop every 60s with `max_markets=8`, `max_concurrency=2`, `timeout_seconds=4`, and a status file at `crypto_options_app/artifacts/automation/market_activity_capture_status.json`; initial monitoring reached iteration 3 with no data-service freshness blockers and no Codex manual-order authority.
- Live trading: app-level live/order flags may be enabled for policy-gated automated testing; orders can only flow through promotion-manager, executor, budget/risk, lifecycle, reconciliation, stop, and demotion gates. Codex manual orders remain prohibited.
- Best simple candidate: `profile_splus_hedger_follow_hold_60s_v10 + profile_group_quality`, currently below the 12-sample promotion floor.
- Master hedge-grid track: valid north star, but protected-floor variants replay only when closed-cycle candidates exist.
- Storage audit latest: `postgres_plus_redis_hot_plane_candidate`; Postgres remains durable truth, Redis is disabled and gated behind tested TTL cache/lock adapter semantics. Current measured pressure remains Postgres memory over 6GiB; latest endpoint timings were health ~24ms, dashboard/control-center ~47ms with ~1.0MB payload, signals validation status ~1420ms with ~988KB payload, and strategies promotion ~49ms with ~255KB payload.
- Verification latest: storage/Postgres/runtime audit tests passed; health reports operational status `ok`, Postgres runtime, and A/B/C/D freshness `ok`. App live/order flags are allowed for policy-gated live testing, while live readiness remains governed by strategy-specific criteria, lifecycle, reconciliation, and sufficient live/shadow evidence.
- Runtime audit latest: 0 production runtime direct-connect SQLite blockers, 0 review-required SQLite usages, 11 allowed migration/test/compat usages, and 0 loaded forbidden legacy `app.*` crypto modules from canonical app import; safe audit artifact at `crypto_options_app/artifacts/reports/runtime_audit_latest.md`. Current audit is warning-level `degraded` because the optional separate frontend service is offline and Postgres memory remains high, not because of a runtime blocker.
- Transition readiness review: `degraded`, broad automation `not_ready`, no blockers, no `health_degraded` warning, no accidental live candidates. Remaining warnings are `repo_dirty_requires_inventory_cleanup` and `storage_audit_degraded`. The live `/strategies/promotion` endpoint exposes `crypto_options_promotion_policy_contract_v1`.
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
- Batch 4 compatibility wrapper decision plan: `crypto_options_app/artifacts/reports/compatibility_wrapper_decisions_latest.md`; active import blockers cleared, non-active wrapper decisions reviewable, GitHub source-of-truth is ready, and both current fixed-chat lanes are eligible to start. Non-active wrapper archive/replacement still requires focused tests and no bulk deletion.
- Batch 4 cutover progress: active crypto callers now use `crypto_options_app.data_nodes.polymarket_crypto`, `crypto_options_app.data_nodes.crypto`, `crypto_options_app.pipelines.options`, `crypto_options_app.services.crypto_options`, `crypto_options_app.runtime.local_paths`, `crypto_options_app.api.db`, and `crypto_options_app.trading.polymarket_portfolio` instead of old `app.*` runtime imports.
- Fixed chat prompt folder: `crypto_options_app/artifacts/team_coordination/fixed_chat_prompts/`. Frontend fixed chat can start from `fixed_chat_prompts/frontend_control_center_developer.md`; signal/strategy cleanup can start from `fixed_chat_prompts/signal_strategy_management_cleanup.md`.
- Promotion state: 110 strategies, 12 shadow-ready, 0 live candidates, 0 strict signal blockers.
- Promotion policy contract: strategy promotion summaries expose `crypto_options_promotion_policy_contract_v1`; `PASSED`/`SELECTED`/`STRUCTURAL_PASS`/`STRUCTURAL_ALTERNATE` are not promotable signal states. Strategy specs can define their own promotion/demotion criteria, including lower win-rate paths when paired with stronger PnL/loss gates. Chat judgment cannot promote; the app promotion manager applies the criteria.
- Transition readiness now blocks if the strategy promotion endpoint lacks `crypto_options_promotion_policy_contract_v1`.
- Frontend strategy lab now renders the `policy_contract` from `/strategies/promotion`, including live-candidate gates, the `PROMOTION_READY` signal gate, non-promotable signal labels, strict blocker allowance, and strategy-policy-only live authority.
- Strategy revision scout now consumes `crypto_options_promotion_policy_contract_v1`; it reports non-promotable signal labels by state/type/source and uses policy-derived live-candidate thresholds instead of hard-coded queue cleanup assumptions.
- Signal/strategy cleanup batch CLI is available: `python -m crypto_options_app.scripts.run_crypto_options_signal_strategy_cleanup_batch --max-signals 24 --max-strategies 12 --exclude-done-handoff`. It is read-only, excludes completed signal-strategy handoff rows when requested, and emits bounded cleanup classifications for fixed chats and future queue workers.
- First bounded cleanup batch artifact: `crypto_options_app/artifacts/reports/signal_strategy_cleanup_batch_latest.md`, generated at `2026-06-07T06:56:17Z` with 12 signal rows and 6 strategy rows. Overall queue shape: 235 signals (`125 NEEDS_VARIANT`, `97 PROMOTED` cleanup classification only, `13 STRICT_REPLAY_REQUIRED`) and 90 strategies (`1 BLOCKED`, `54 NEEDS_VARIANT`, `35 SHADOW_REQUIRED`). No live authority is implied.
- Fixed chat startup readiness artifact: `crypto_options_app/artifacts/reports/fixed_chat_startup_readiness_latest.md`; status `ready`, `2/2` current fixed chats ready, future specialist prompts remain future-only, and no fixed chat may place Codex manual orders or bypass gates.
- Limited automation startup readiness artifact: `crypto_options_app/artifacts/reports/automation_startup_readiness_latest.md`; status `ready_to_schedule`, `3/3` planned limited automations ready, `create_immediately=false`, and no limited automation may place Codex manual orders or bypass gates.
- Limited automations active/paused: `crypto-options-db-data-observability` every 15 minutes, `crypto-options-signal-strategy-queue-worker` every 5 minutes, and `crypto-options-frontend-status-reporter` hourly are active. The signal worker is limited to Spark signal-revision cleanup with `--exclude-done-handoff`; strategy reviewer/creator automations remain gated. Existing `crypto-options-unified-dev-loop` heartbeat remains paused in the app automation store; current master control is this active chat goal/thread.
- Limited automation report freshness artifact: `crypto_options_app/artifacts/reports/automation_report_status_latest.md`; current status `fresh`. DB/data observability, signal/strategy queue worker, and frontend status reporter all have fresh latest reports.
- Signal/strategy queue worker latest proposals: keep `buying_ahead_pre_event_v1` blocked until a pre-event universe/feed handoff exists; retire `a_fallback_outcome_probe_v1` from queue-management consideration until a replacement fallback design or curated bundle swap is explicitly requested; keep `crypto_direction_option_context_hold_60s_v1` as `SHADOW_REQUIRED` until a bounded forward-mark shadow sampler/evidence pass is explicitly handed off.
- DB/data observability latest: Postgres runtime reads are ok, Redis remains disabled, runtime SQLite audit is ok, and the stale C ETH core data blocker was repaired in this master pass. Root cause: C option capture used a global bounded target slice, so `--max-tokens 8` selected BTC targets first and starved ETH. The service now preserves complete symbol pairs under the cap, C ETH events/readiness recovered, and B Profiles resumed after current ETH events became available. System health now computes data-service watermark freshness from `last_run_at_utc`, so stale D `polymarket_live_activity_capture` watermarks are explicitly downgraded from raw `healthy` to `stale`. The D market-activity capture query was repaired for Postgres grouped ordering, and a bounded read-only D loop is now refreshing the watermark with no data-service freshness blockers. Remaining DB/data warning is Postgres memory over 6GiB; B profile source rows can still carry coverage warnings, but the current service watermark is fresh.
- Frontend status latest: backend-rendered UI and key API contracts are reachable on canonical `8011`; separate frontend service `8012` is currently offline, so fixed-chat frontend work should treat `8011` as the working target until a separate React/frontend service is intentionally started.

## Active Fixed Chats

- Signal And Strategy Management Cleanup: eligible to start from `fixed_chat_prompts/signal_strategy_management_cleanup.md`; primary GitHub issues #155-#159.
- Frontend Control Center Developer: eligible to start from `fixed_chat_prompts/frontend_control_center_developer.md`; primary GitHub issues #160-#164; must avoid backend, DB, promotion, and trading runtime changes.

## Master Rules

- Do not promote from chat judgment alone; strategy scripts/specs define criteria and the promotion manager applies them.
- Promotion/demotion must come from system evidence and policy.
- No Codex manual orders.
- Global/API live and order flags may be true for policy-gated automated testing.
- Data services, signal validation, strategy backtest, and live-replay can feed the promotion manager; live execution is restricted to strategy-defined criteria plus executor, budget/risk, lifecycle, reconciliation, stop, and demotion gates.

## 2026-06-08T00:30Z Live State Machine Correction

- The active pipeline no longer has a separate human/Codex approval concept.
- Strategy scripts/specs define promotion, demotion, scaling, PnL, win-rate, loss-streak, lifecycle, reconciliation, drift, and live-execution criteria.
- If a strategy passes backtest, the system queues shadow/live-replay automatically.
- If a strategy passes shadow/live-replay, the promotion manager emits `LIVE_CANDIDATE` and the queue worker runs `live_candidate` automatically through app runtime gates.
- If a strategy fails backtest, shadow/live-replay, or live criteria, the system marks it for review, block, or demotion according to the strategy policy.
- Legacy `supervised_live_candidate` rows remain recognized only as a compatibility alias for `live_candidate`.
- The old CLI launch-authority flag was removed from the active queue/scheduler CLIs.
- Codex and fixed-chat automations may create/review strategy and signal code, but may not place Codex manual orders or bypass app gates.

## Next Transition Actions

1. Commit the Batch 4 decision plan so non-active wrapper handling is reviewable.
2. Start fixed chats from their prompt files and linked GitHub issues when user is ready; verify `fixed_chat_startup_readiness_latest.md` remains `ready` and `transition_readiness_latest.md` has no blockers first.
3. Keep monitoring `automation_report_status_latest.md`; do not add or reactivate any broader standing automation unless these three limited lanes remain fresh and useful.
4. Keep Batch 0 held runtime/data/generated artifacts unstaged unless explicitly promoted to source-of-truth.
5. Keep legacy SQLite profile/market side stores fenced to old research CLIs only; their defaults now point to `local/shared/artifacts/crypto-options-research/...`, not the central runtime DB path.
6. Keep Redis disabled until a measured hot-plane use case is selected; use only TTL cache/queue-lock semantics through the adapter, never durable trading truth.
7. Let the Signal/Strategy Management Cleanup fixed chat start from `signal_strategy_cleanup_batch_latest.md` and GitHub issues #155-#159; first work should retire or create justified V2-V5 variants for the first weak hedge-grid signal rows and revise/retire the first six stale strategy rows.
8. Continue issue #153 by wiring the same cleanup classifications into a bounded queue-worker automation after the fixed-chat workflow proves the handoff path.
9. Use `fixed_chat_bootstrap.md` and `fixed_chat_prompts/README.md` before starting any fixed chat.
10. Keep Postgres memory pressure under review; current health/readiness must stay bounded and Redis remains disabled unless a measured hot-plane use case is selected.
11. Continue Batch 4 compatibility-wrapper review and repo cleanup discipline; keep generated/runtime/data artifacts unstaged.
12. Let fixed chats operate only from their prompt files, GitHub issues, and `team_coordination` handoffs; no fixed chat or automation may place Codex manual orders or bypass app promotion/runtime gates.

## 2026-06-07T11:21Z Signal Status Load Reduction

- Signal validation status now supports a summary mode: `/v1/crypto-options-app/signals/validation/status?include_signals=false`.
- Full signal rows remain available by default for UI/detail use, and `signal_limit` can bound embedded rows for focused reads.
- Storage and transition readiness audits now call summary mode. Latest storage audit measured `signals_validation_status` at 86.31 ms and 596 bytes, down from roughly 1.4-1.5 s and ~988 KB.
- Fixed chat startup readiness remains `ready` for both immediate fixed chats, with no Codex manual-order or gate-bypass authority.
- Remaining storage warning is Postgres memory over 6GiB; next pressure targets are dashboard/control-center payload size and Postgres memory behavior, not signal status.

## 2026-06-07T11:29Z Control Center Summary Mode

- Control-center state now supports `include_details=false` for audits and automation status checks while preserving the full default payload for the working UI.
- Summary mode strips embedded positions, orders, history, event rows, profile-distribution rows, portfolio ledger rows, and crypto indicator detail lists, while preserving counts, modules, module blocks, live safety flags, and high-level state.
- Storage and transition readiness audits now call `/dashboard/control-center-state?include_details=false`.
- Latest storage audit measured `dashboard_control_center_state` at 44.36 ms and 15,993 bytes, down from roughly 1.0 MB.
- Remaining storage degradation is Postgres memory over 6GiB. Redis remains disabled and non-authoritative until a measured hot-plane use case survives adapter tests.

## 2026-06-07T11:36Z Storage Diagnostics Decision

- Storage audit now includes bounded Postgres diagnostics: connection states, long active query count, selected memory settings, database size, temp-file counters, and largest tables.
- Latest decision changed from Redis candidate to `postgres_only_for_now`: high Docker/Postgres memory alone is not evidence that Redis will help.
- Current diagnostic state: 11 GB database, 6 total connections, 1 active connection, 0 long active queries, and cumulative temp bytes over 1 GB.
- Current largest tables are `strategy_validation_runs`, `polymarket_order_book_levels`, `profile_distribution_snapshots`, and `profile_raw_activity`.
- Next DB/data lane should review temp-file sources and retention/materialization for large replay/source tables before any worker widening. Redis remains disabled.

## 2026-06-07T11:45Z Repo Cleanup Gate Reconciliation

- Repo cleanup inventory and batch reports now delegate fixed-chat startup to `fixed_chat_startup_readiness_latest.json` instead of keeping stale hard-coded blocked gates.
- Latest cleanup inventory/batch reports show `fixed_chats_start_ready=true` and `github_issue_creation_ready=true`, while automatic file moves remain disabled and Batch 4 compatibility wrappers remain review-only.
- Approved fixed chats remain only `frontend_control_center_developer` and `signal_strategy_management_cleanup`; future specialist prompts stay future-only until the master chat opens those lanes.

## 2026-06-07T14:35Z Control Surface Trust Sweep

- Canonical API remains online on `8011`; `/health` reports `status=ok`, Postgres runtime, `orders_allowed=true`, `live_trading_authorized=true`, `manual_orders_allowed=false`, and `manual_orders_avoided=true`.
- Dashboard/control-center state now refreshes Postgres before using an expired cache. Expired control-state cache is used only after a DB read error, preventing stale flags/portfolio rows from overriding runtime truth.
- Root dashboard now reports policy-gated live testing enabled and automated live orders enabled instead of stale `Gated read only` / `orders disabled` badges. Codex manual orders remain prohibited.
- Current portfolio metrics exclude stale historical live-dashboard artifacts when no active live run is current. Current realized PnL and open cost now render as `$0.00`; historical `-$8.01` realized PnL and `$2.09` open cost remain visible on Portfolio as excluded historical evidence.
- Latest event display now prefers completed usable price paths and renders a sparkline from `event_price_points`; browser verification found `eth-updown-5m-1780842000` with 17 snapshots and 9 bucket points.
- Profile panel now prefers robust recent profile-distribution snapshots over thin newest snapshots. Browser verification showed a robust row with 19 profiles / 28 components and explicit display-quality text.
- Strategy runtime rows from stale live tests now render as `HISTORICAL EXECUTED` with a note that they are retained as evidence, not current live exposure.
- Browser smoke check covered Command, Events, Portfolio, Positions, Orders, History, Signals, Strategies, and System tabs with no endpoint-load errors.
- Tests: `python -m pytest tests/crypto_options_app/test_app_skeleton_pytest.py tests/crypto_options_app/test_strategy_promotion_manager_pytest.py tests/crypto_options_app/test_system_integrity_health_pytest.py tests/crypto_options_app/test_automation_report_status_pytest.py tests/crypto_options_app/test_fixed_chat_startup_readiness_pytest.py -q` -> 88 passed.

## 2026-06-07T14:55Z Spark Signal-Revision Worker Trial

- Reactivated existing `crypto-options-signal-strategy-queue-worker` instead of creating a duplicate automation.
- Temporary evaluation settings: `gpt-5.3-codex-spark`, high reasoning, `FREQ=MINUTELY;INTERVAL=5`.
- Scope narrowed to signal revision candidates only. Current observed queue shape: 235 signals, 115 revision candidates, 0 pending review.
- Worker must process one revision candidate per pass when possible; it may retire, block, request strict replay/shadow, mark needs-variant, or create one small justified next Vn if the fix is local and testable.
- Worker must hand off replay/lifecycle/reconciliation/queue/promotion/frontend/DB technical issues to GitHub/log/queue instead of patching them.
- Live policy: app-level live/order flags may be true for strategy-policy-gated testing, but this worker cannot bypass gates or place Codex manual orders.
- Evaluation target: monitor a few 5-minute passes through `automation_status/signal_strategy_queue_worker_latest.md` and `handoff_queue.jsonl`, then decide whether Spark quality is high enough to keep this cadence.

## 2026-06-07T16:51Z Spark Signal-Revision Worker Halt

- Operator halted the Spark signal-revision cleanup automation after the first trial passes.
- Current state: `crypto-options-signal-strategy-queue-worker` is paused; model/prompt/schedule are preserved for later review.
- Reactivation gate: do not re-enable until this master chat completes an integrity check covering the control-center signal/strategy status view, promotion/demotion boundaries, and whether Spark only handled assigned review/revision rows.
- No manual orders were authorized by the pause. App-level live testing flags remain policy-owned by the runtime, not by the worker.

## 2026-06-07T17:15Z Spark Signal-Revision Integrity Gate

- Signal catalog item route repaired and live-verified. Root cause was serializing the `structural_blockers` method instead of calling it; `/signals/catalog/{signal_id}` now returns spec/script metadata and an empty structural blocker list for the checked V4 row.
- Duplicate cleanup feed mitigated. The cleanup batch CLI and prompt now support `--exclude-done-handoff`; the paused Spark worker prompt uses it and must hard-stop with `duplicate_batch_feed` if a completed signal-strategy handoff row is selected again.
- Control surface browser check passed on canonical `8011`: loop badges now show `57 ready / 115 revisions`, `12 shadow-ready / 100 replay rows`, and `0 live-running`; Operating Report rows show signal counts by `PROMOTION_READY`, `REVISION_REQUIRED`, `STRUCTURAL_ALTERNATE`, `RETIRED`, and strategy counts by `SHADOW_READY`, `SHADOW_REVIEW`, `NEEDS_BACKTEST`, `BACKTEST_READY`, `REVIEW_BLOCKED`.
- Current explanation for `0 live-running`: strategies are not live because none are in `LIVE_RUNNING`; `12` are only `SHADOW_READY` and still need system-managed shadow/live-replay evidence before promotion.
- Focused tests: `python -m pytest tests/crypto_options_app/test_app_skeleton_pytest.py tests/crypto_options_app/test_signal_strategy_cleanup_batch_pytest.py tests/crypto_options_app/test_strategy_promotion_manager_pytest.py -q` -> 53 passed.
- Reactivation decision: `crypto-options-signal-strategy-queue-worker` was re-enabled for another small Spark trial. Strategy reviewer/creator automations remain gated until the signal worker completes additional clean passes without duplicate rows, scope creep, or technical handoff blockers.

## 2026-06-07T17:58Z Priority Profile-Follow Strategy Expansion

- Master strategy lane resumed after the transition/automation setup pause.
- Created four priority profile-follow candidate variants:
  - `profile_splus_hedger_follow_hold_60s_v11`
  - `profile_splus_hedger_follow_hold_60s_v12`
  - `profile_splusplus_hedger_follow_hold_60s_v4`
  - `profile_splus_hedger_follow_hold_60s_v13`
- All four are `shadow_test`, `promotion_candidate_lane=true`, `priority_queue=profile_follow_candidate_top`, and use strategy-defined promotion policy metadata instead of chat judgment.
- Strategy policy for these candidates allows the lower 60%+ win-rate family only with positive PnL, bounded live loss, lifecycle/reconciliation/drift gates, and a 12-sample minimum.
- Added a forward-cashout-edge prefilter to `profile_group_quality` so replay/scout avoids rows that would be blocked by the strategy's own forward-edge criteria.
- Added bounded transient-DB retry around strategy scenario loading after a Postgres deadlock during scout. GitHub issue #145 comment 4643476340 and `technical_issue_handoff_log.md` record the handoff.
- First bounded result:
  - `profile_splusplus_hedger_follow_hold_60s_v4` matched one clean `profile_group_quality` scenario on event `566130`.
  - Historical replay passed: 1 simulated execution, 0 blockers.
  - Live-replay passed: 1 simulated execution, 0 blockers.
  - Promotion manager state: `SHADOW_READY`, `1/12` recent economic samples, `+$0.42493356` recent simulated PnL, `100%` recent win rate, blocker `recent_shadow_live_sample_below_floor`.
- Current decision: prioritize accumulating fresh `profile_group_quality` samples for `profile_splusplus_hedger_follow_hold_60s_v4` and the V10/V11/V12/V13 siblings. Do not start live until the system promotion manager clears sample, PnL, lifecycle, reconciliation, signal-blocker, and drift gates.
- Spark automations active:
  - Signal revision: every 5m, `gpt-5.3-codex-spark`.
  - Strategy revision: every 10m, `gpt-5.3-codex-spark`.
  - Strategy creator: every 15m, `gpt-5.3-codex-spark`.
- Focused tests:
  - `python -m pytest tests\crypto_options_app\test_strategy_manager_pytest.py -k "registry_contains or profile_consensus" -q` -> passed.
  - `python -m pytest tests\crypto_options_app\test_strategy_live_replay_worker_pytest.py -k "profile_group_quality" -q` -> passed.
  - `python -m pytest tests\crypto_options_app\test_strategy_promotion_manager_pytest.py -k "strategy_defined_criteria or policy" -q` -> passed.
  - `python -m pytest tests\crypto_options_app\test_strategy_promotion_manager_pytest.py tests\crypto_options_app\test_strategy_live_replay_worker_pytest.py -k "profile_group_quality or strategy_defined_criteria or policy" -q` -> passed.
- Live/manual-order status: no Codex manual orders. App live capability remains gate-driven by strategy policy and runtime/promotion infrastructure.

## 2026-06-07T18:18Z Progress Auditor And Replay Integrity Check

- Evaluated whether the system should wait for more automation/backtest/shadow feedback or halt due to mechanical defects.
- Runtime:
  - Initial short-limit health and promotion reads timed out while Postgres was around `47.87%` CPU and `7.171GiB / 15.47GiB` RAM.
  - Immediate follow-up reads recovered: health about `1.69s`, promotion about `0.29s`, and Postgres active-query probe showed no long active queries.
  - Decision: transient warning, not a blocker. Keep work bounded and do not widen replay concurrency.
- Automation performance:
  - Signal revision Spark worker produced a valid bounded pass at `2026-06-07T18:10:49Z`, retiring one weak signal row without scope creep.
  - Strategy revision Spark worker produced a valid bounded pass at `2026-06-07T18:08:38Z`, setting two strategy rows to `SHADOW_REQUIRED` / `STRICT_REPLAY_REQUIRED` without code changes.
  - Strategy creator Spark worker had not yet produced its first real report.
- Replay integrity:
  - Found one failing focused replay test. Root cause was a stale fixture expecting a fill where the strategy's limit order did not cross the ask.
  - Patched the fixture to use executable quotes instead of weakening replay semantics.
  - Strategy revision and creator workers were paused during the replay triage and re-enabled after tests passed.
- Tests:
  - `python -m pytest tests\crypto_options_app\test_strategy_promotion_manager_pytest.py -q` -> 16 passed.
  - `python -m pytest tests\crypto_options_app\test_signal_validation_runtime_pytest.py -k "queue_claims_one_item_and_respects_ttl" -q` -> 1 passed.
  - `python -m pytest tests\crypto_options_app\test_strategy_live_replay_worker_pytest.py -k "limit_buy or profile_group_quality or forward_price_path" -q` -> 4 passed.
- Created `crypto-options-automation-progress-auditor`:
  - Model: `gpt-5.3-codex-spark`.
  - Cadence: every 30 minutes.
  - Role: decide `WAIT`, `HALT_AFFECTED_AUTOMATION`, `HANDOFF_TECHNICAL_ISSUE`, `PATCH_REQUIRED`, or `DO_NOT_WIDEN`.
- Automation freshness integration:
  - `automation_report_status.py` now tracks six report files: DB/data, signal revision, strategy revision, strategy creator, progress auditor, and frontend status.
  - DB/data and frontend reporter automations were found paused and re-enabled as report-only lanes. Frontend reporter was moved to `gpt-5.3-codex-spark`.
  - Their latest markdown files remain stale until the next scheduled pass writes fresh reports.
- Current decision: wait for more bounded automation/replay evidence, but do not run 100 simultaneous strategies. Current safe pattern remains small replay batches until a resource-aware scheduler exists.
- Live/manual-order status: no direct live child started by Codex; Codex manual orders avoided.

## 2026-06-07T21:05Z Spark Worker Effectiveness Audit

- Paused/confirmed paused:
  - `crypto-options-signal-strategy-queue-worker`.
  - `crypto-options-strategy-revision-spark-worker`.
  - `crypto-options-strategy-creator-spark-worker`.
- Window audited: 2026-06-07T17:58Z through 2026-06-07T21:05Z.
- Signal worker output: 12 signal rows reviewed and proposed as `RETIRED`, but canonical `/signals/validation/status` still shows those processed rows as `NEEDS_V2_REVIEW`. Effective canonical discards: 0.
- Strategy revision output: 11 unique strategy ids / 14 strategy actions reviewed, mostly `BLOCKED`, `SHADOW_REQUIRED`, or `STRICT_REPLAY_REQUIRED`; effective canonical discards: 0.
- Strategy creator output: created `profile_splus_hedger_follow_hold_60s_v14` and `profile_splus_hedger_follow_hold_60s_v15` in code, but running `/strategies/promotion` does not list either variant. Effective runtime-created strategies: 0 until registry sync/reload exists.
- Current strategy promotion summary: 117 strategies, 45 with historical replay pass count, 85 with live-replay pass count, 1 with recent shadow-live economic samples, 0 live-running.
- Current signal summary: 235 signals, 125 `NEEDS_V2_REVIEW`, 97 `PROMOTION_READY`, 13 `STRUCTURAL_PASS`.
- Live/manual-order status: app live flags are enabled on health/strategy endpoints, manual orders remain disabled/avoided, and no active live strategy was observed in the audit window.
- Blocking decision: do not re-enable the three Spark production workers until canonical decision persistence, duplicate-claim exclusion, runtime strategy registration sync, endpoint live-flag consistency, and bounded replay/shadow scheduling are fixed and tested.
- Detailed audit: `crypto_options_app/artifacts/team_coordination/automation_status/spark_worker_effectiveness_audit_20260607T2105Z.md`.

## 2026-06-07T21:25Z Strategy Pipeline Stall Audit

- All `crypto-options-*` automations were paused before inspection.
- Current app runtime has data services and API running, but at this point lacked a durable strategy backtest/shadow/live-candidate queue drain.
- After forcing `/strategies/promotion?refresh=1`, current state is:
  - `NEEDS_BACKTEST`: 41
  - `BACKTEST_READY`: 2
  - `SHADOW_READY`: 11
  - `SHADOW_REVIEW`: 58
  - `REVIEW_BLOCKED`: 1
  - `LIVE_CANDIDATE`: 0
  - `LIVE_RUNNING`: 0
- Root cause: strategy replay workers are one-shot scripts, not a state-driven scheduler. They do not automatically claim `NEEDS_BACKTEST`, `BACKTEST_READY`, or `SHADOW_READY` rows.
- Why `NEEDS_BACKTEST` persists: nothing automatically runs historical replay for those rows, and default replay CLI strategy ids are a fixed five-strategy list unless explicitly overridden.
- Why `BACKTEST_READY` persists: no scheduler claims those two rows and runs live-replay/shadow.
- Why `SHADOW_READY` persists: all rows are missing recent one-hour economic proof; no recurring live-replay sampler accumulates 12 current economic samples.
- Why no live trading happened: no strategy reached `LIVE_CANDIDATE`, and the live-candidate phase had not yet been wired into the queue worker.
- Additional defect: local code loads 119 strategies including `profile_splus_hedger_follow_hold_60s_v14`/`v15`, but the running API exposes 113 and does not list those two variants. Strategy creation needs registry sync/reload.
- Detailed audit: `crypto_options_app/artifacts/team_coordination/automation_status/strategy_pipeline_stall_audit_20260607T2125Z.md`.

## 2026-06-07T21:35Z Strategy Pipeline Scheduler Patch

- Implemented bounded strategy pipeline scheduler:
  - `crypto_options_app/workers/strategy_pipeline_scheduler.py`
  - `crypto_options_app/scripts/run_crypto_options_strategy_pipeline_scheduler.py`
  - `tests/crypto_options_app/test_strategy_pipeline_scheduler_pytest.py`
- The scheduler connects the missing phases:
  - `NEEDS_BACKTEST` -> historical replay.
  - `BACKTEST_READY` -> shadow/live replay.
  - `SHADOW_READY` -> recent shadow/live-replay sample accumulation.
  - `LIVE_CANDIDATE` -> live-candidate queue item executed automatically through strategy-defined app runtime gates.
- Added Docker Postgres resource guard before replay execution. Default guard skips work when Postgres exceeds `350%` CPU or `80%` memory.
- Bounded live-state validation runs:
  - Historical replay: 3 profile-follow strategies, 1 passed / 2 blocked.
  - Shadow replay: `profile_splus_hedger_follow_hold_60s_v13`, 1 passed / 0 blocked; row advanced to `SHADOW_READY`.
  - Recent shadow sample: 3 profile-follow rows, 1 passed / 2 blocked.
- Current state after scheduler slices: `NEEDS_BACKTEST` and `SHADOW_READY` still require continued scheduled replay; no `LIVE_CANDIDATE` or `LIVE_RUNNING` observed.
- Resource note: after bounded replay, Docker Postgres briefly reported high CPU; keep scheduler to one replay action per 30-minute pass until resource behavior is stable.
- Tests: `python -m pytest tests/crypto_options_app/test_strategy_pipeline_scheduler_pytest.py tests/crypto_options_app/test_strategy_promotion_manager_pytest.py::test_strategy_policy_override_supports_low_win_rate_high_pnl_path_pytest -q` -> 5 passed.
- Live/manual-order status: no live submission attempted in this pass; this older scheduler path has been superseded by queue-driven `live_candidate`; Codex manual orders avoided.
- Detailed implementation note: `crypto_options_app/artifacts/team_coordination/automation_status/strategy_pipeline_scheduler_implementation_20260607T2135Z.md`.

## 2026-06-07T21:58Z Strategy Pipeline Queue Worker Correction

- User correction accepted: strategy phase progression must not depend on a periodic scheduler discovering eligible rows.
- Implemented durable queue semantics:
  - Added `strategy_pipeline_queue` to canonical schema and Postgres readiness checks.
  - Strategy registry sync now enqueues an immediate `historical_backtest` item for each registered strategy/version.
  - Added queue helpers in `crypto_options_app/strategies/pipeline_queue.py`.
  - Added bounded worker `crypto_options_app/workers/strategy_pipeline_queue_worker.py`.
  - Added CLI `python -m crypto_options_app.scripts.run_crypto_options_strategy_pipeline_queue_worker`.
- Active automation changed:
  - Paused deprecated `crypto-options-strategy-pipeline-scheduler`.
  - Created active `crypto-options-strategy-pipeline-queue-worker` at 30-minute cadence with `gpt-5.3-codex-spark`.
- Correct architecture: registration/enrollment and promotion-state advancement create durable work immediately; the 30-minute automation only drains one queued item with Postgres resource guards.
- Repeatable recent shadow sampling stays queued until the promotion manager moves the strategy to `LIVE_CANDIDATE` or a review/block state.
- Focused tests passed: `python -m pytest tests/crypto_options_app/test_strategy_pipeline_queue_worker_pytest.py tests/crypto_options_app/test_automation_report_status_pytest.py tests/crypto_options_app/test_strategy_manager_sync_pytest.py::test_strategy_registry_sync_persists_specs_versions_and_readiness_pytest tests/crypto_options_app/test_db_schema_pytest.py::test_canonical_db_initializes_all_schema_groups_pytest -q` -> 10 passed.
- Runtime queue smoke:
  - Dry-run backfilled current promotion-state backlog into `60` durable queue items: `45` historical backtest, `2` shadow replay, `13` repeatable recent shadow sample.
  - One real bounded queue-worker pass claimed `recent_shadow_sample:profile_splus_hedger_follow_hold_60s_v13:v13`, executed `1` simulated order/fill/PnL lifecycle with `1` passed / `0` blocked, and re-queued the same phase because the strategy remains `SHADOW_READY`.
  - Latest report: `crypto_options_app/artifacts/team_coordination/automation_status/strategy_pipeline_queue_worker_latest.md`.
- Live/manual-order status: no live submission attempted in this patch; queue items remain live-capable through app runtime gates; Codex manual orders avoided.

## 2026-06-07T23:07Z Outage Recovery And Queue Fairness Patch

- Recovery after the provider/power interruption:
  - `/v1/crypto-options-app/health` is `ok`.
  - Runtime backend remains Postgres.
  - A/B/C/D source status files are fresh and healthy.
  - API process and all five data-service loops are running.
  - Docker Postgres container is healthy, but currently hot: sampled around `450%` CPU and `7.4 GiB` RAM, so manual heavy replay was not forced.
- Frontend/browser verification:
  - Root UI shows policy-gated live testing/order capability, manual orders disabled, and strategy queue counts.
  - Operating report text includes `120 registered; 61 queued, 0 running, 14 shadow-ready, 0 live-running`.
  - Trading row shows `0 live candidates, 0 live-running; Codex manual orders remain prohibited`.
- Queue blocker found:
  - `profile_splus_hedger_follow_hold_60s_v16` recent-shadow sampling was high priority and repeatable.
  - Because the item requeued itself after each positive sample, it could monopolize 30-minute queue-worker passes and leave untouched historical backtests visibly queued.
- Patch:
  - `claim_strategy_pipeline_work` now applies a fairness penalty to `recent_shadow_sample` items after `attempt_count > 0`.
  - Claim ordering now prefers `historical_backtest`, then `shadow_replay`, then `recent_shadow_sample`, then `live_candidate` when effective priority ties.
  - Regression test added to prove a recurring recent-shadow item cannot starve a fresh historical backtest.
- Verification:
  - `python -m pytest tests/crypto_options_app/test_strategy_pipeline_queue_worker_pytest.py -q` -> 5 passed.
  - Next claim-order check now selects `historical_backtest:profile_splus_hedger_follow_hold_60s_v11:v11`, not the recurring V16 recent-shadow sample.
- GitHub source of truth:
  - Issue [#171](https://github.com/LucaCGN/janus_cortex/issues/171) tracks the queue starvation/fairness bug and follow-up validation.
- Live/manual-order status: live flags remain enabled for system-gated testing, no live submission was attempted in this pass, and Codex manual orders were avoided.

## 2026-06-07T23:19Z Queue Validation And Empty-Selector Backtest Fix

- Real queue-worker validation after the fairness patch:
  - Claimed `historical_backtest:profile_splus_hedger_follow_hold_60s_v11:v11`, proving the queue no longer loops on the recurring V16 recent-shadow sample.
  - The first run exposed a mechanical backtest bug: `IndexError: tuple index out of range` when `profile_group_quality` returned no matching scenarios.
- Patch:
  - `strategy_backtest_replay.py` now mirrors live replay behavior for empty selector results and returns a structured `scenario_selector:no_matching_scenarios` blocker instead of crashing.
  - Queue action payloads with zero scenarios and blockers now become `blocked`, not false `executed` work.
- Runtime result:
  - Requeued the V11 item that had failed with the internal exception.
  - Reran one bounded queue item.
  - V11 now records `scenario_selector:no_matching_scenarios`, queue status `blocked`, no live submission attempted, and manual orders avoided.
  - Queue summary is now `60` queued, `1` blocked, `2` done.
  - Next claim target is `historical_backtest:profile_splus_hedger_follow_hold_60s_v12:v12`.
- Browser verification:
  - Operating Report now shows `120 registered; 60 queued, 0 running, 14 shadow-ready, 0 live-running`.
  - Header summary shows `14 shadow-ready / 60 queued`.
- Tests:
  - `python -m pytest tests/crypto_options_app/test_strategy_live_replay_worker_pytest.py::test_strategy_backtest_replay_worker_handles_empty_selector_without_crash_pytest tests/crypto_options_app/test_strategy_pipeline_queue_worker_pytest.py -q` -> 7 passed.
- GitHub source of truth:
  - Issue [#171](https://github.com/LucaCGN/janus_cortex/issues/171) comment `4644398710`.
- Live/manual-order status: live flags remain enabled for system-gated testing; no live submission was attempted; Codex manual orders were avoided.

## 2026-06-08T00:08Z Live Candidate Path Diagnosis

- Current answer to "why no live trading yet": the flow is working through historical replay and shadow/live-replay, but no strategy has enough current recent shadow-live evidence to become `LIVE_CANDIDATE`.
- App state:
  - `/health` is `ok`.
  - Runtime DB is Postgres.
  - A/B/C/D data freshness is green.
  - Automated strategy-gated order flow is enabled.
  - Codex manual orders remain prohibited.
- Strategy state after the focused queue drain:
  - `15` SHADOW_READY.
  - `2` BACKTEST_READY.
  - `44` NEEDS_BACKTEST.
  - `58` SHADOW_REVIEW.
  - `0` LIVE_CANDIDATE.
- Fresh evidence:
  - `profile_splus_hedger_follow_hold_60s_v14` passed historical replay and one shadow/live-replay sample; current recent evidence is `1` sample, `+$0.29345649`, `100%` win rate.
  - `profile_splusplus_hedger_follow_hold_60s_v4` passed one current recent sample; current recent evidence is `1` sample, `+$0.2928068`, `100%` win rate.
  - `profile_splus_hedger_follow_hold_60s_v16` had `3` clean live-replay samples and `+$0.88036947`, but they aged out of the one-hour recent-evidence window.
- Root blocker:
  - Promotion policy requires 10-18 recent samples inside a 3600-second window.
  - The active queue worker was configured for one item per 30 minutes, which cannot fill that window.
- Next required action:
  - Keep review/creator automations slow, but increase the strategy pipeline queue worker throughput under Postgres resource guard.
  - Once a strategy reaches `LIVE_CANDIDATE`, the queue worker should run the `live_candidate` item through strategy-defined app runtime gates.
  - Do not bypass criteria and do not place Codex manual orders.

Action taken:

- Updated `crypto-options-strategy-pipeline-queue-worker` from one item every 30 minutes to up to four bounded queue items every 5 minutes.
- The worker remains Spark-based, resource guarded, and prohibited from Codex manual orders.
- This worker is infrastructure queue drain, not a strategy reviewer/creator automation.

## 2026-06-08T01:25Z Current Runtime Source-Of-Truth Boundary

- Added `crypto_options_app/docs/reference/crypto_options/technical_specs/00_current_runtime_source_of_truth.md`.
- Current policy is now explicit: promotion, live entry, demotion, and scaling are app-owned state-machine actions driven by strategy-defined criteria, not user/Codex approval.
- Historical reports may retain old live/supervised wording as evidence, but they are not active policy unless repeated by the current source-of-truth files.
- Legacy `supervised_live` DB evidence keys and phase aliases are compatibility labels only. The active queue phase is `live_candidate`.
- Codex/fixed-chat work remains limited to creating/reviewing scripts, writing artifacts/issues, and never placing manual orders or bypassing app gates.

## 2026-06-08T01:32Z Live-Candidate Boundary Verification

- Runtime verification:
  - `/health` is `ok`.
  - App order/live flags are enabled for strategy-policy-gated testing.
  - `manual_orders_allowed=false` and `manual_orders_avoided=true`.
  - Control-center state reports `database_backend=postgres`, `db_connection_is_postgres=true`, and labels the configured SQLite path as `sqlite_compatibility_path_not_runtime`.
- Promotion contract verification:
  - `chat_judgment_can_authorize_live=false`.
  - `automation_can_authorize_live=true` only through promotion manager plus executor, budget/risk, lifecycle, reconciliation, stop, and demotion gates.
  - Current promotion counts: `BACKTEST_READY=2`, `NEEDS_BACKTEST=36`, `REVIEW_BLOCKED=1`, `SHADOW_READY=23`, `SHADOW_REVIEW=59`, `LIVE_CANDIDATE=0`.
- Queue reliability patch:
  - Added a process-level lock to `strategy_pipeline_queue_worker` so overlapping automation ticks skip instead of running concurrent queue drains.
  - CLI exposes `--process-lock-stale-seconds`.
  - A second tick now writes a skipped report with blocker `strategy_pipeline_queue_worker_already_running` while the first worker holds the lock.
- Current queue state during verification: `blocked=46`, `done=16`, `queued=14`, `running=1`.
- Focused tests:
  - `python -m pytest tests/crypto_options_app/test_app_skeleton_pytest.py::test_crypto_options_app_builder_mounts_dashboard_routes_pytest tests/crypto_options_app/test_live_preflight_pytest.py tests/crypto_options_app/test_strategy_pipeline_queue_worker_pytest.py tests/crypto_options_app/test_strategy_pipeline_scheduler_pytest.py tests/crypto_options_app/test_strategy_manager_pytest.py -q` -> 28 passed.
- GitHub source of truth: issue `#171`, comment `4644755123`.
- Live/manual-order status: no live candidate existed and no live submission was attempted by this patch; Codex manual orders were avoided.

## 2026-06-08T05:30Z Strategy-Level Live Budget And Active-Order Fix

- Budget correction:
  - Live validation no longer treats a flat per-item cap as the strategy budget model.
  - Profile-follow live candidates now expose strategy-level controls:
    `live_budget_cap_usd`, `live_target_order_notional_usd`,
    `live_min_order_shares`, and `live_min_order_notional_usd`.
  - Current profile-follow validation settings use a `30.0` USD lane budget cap
    and at least `5` shares / `8.0` USD target notional for paired-exit capable
    lanes, so paired limit sells are not starved by undersized buys.
- Runtime correction:
  - The live executor submitted a V13 order that remained `unfilled`.
  - The promotion evaluator incorrectly treated the missing filled-position
    lifecycle row as a review blocker.
  - Promotion evidence now counts active submitted unfilled live orders as live
    lane activity. Missing lifecycle coverage remains a blocker after the order
    resolves without a valid lifecycle row, but not while a live order is still
    active.
- Current validation result:
  - `profile_splus_hedger_follow_hold_60s_v13` is `LIVE_RUNNING` from one
    active live order.
  - V6, V8, and V10 remain `LIVE_CANDIDATE`, but current-market gates blocked
    fresh submissions (`option_entry_price_below_band`,
    `option_path_avg_rolling_60s_range_low`,
    `profile_distribution_top_profile_concentration_high`, or missing S+ hedger
    group evidence).
  - Tail/hedge-grid live candidates blocked on their own tail/rebound/edge gates,
    not on approval state.
- Tests:
  - `python -m pytest tests/crypto_options_app/test_strategy_promotion_manager_pytest.py tests/crypto_options_app/test_strategy_pipeline_queue_worker_pytest.py -q` -> 28 passed.
  - Earlier focused backend suite after sizing metadata patch -> 43 passed.
- Live/manual-order status:
  - App-gated live execution is enabled.
  - Codex manual orders remain prohibited and were avoided.

## 2026-06-08T08:05Z Live Loss Stop And B-Profile Recovery Checkpoint

- Policy checkpoint:
  - Per-strategy budget and per-strategy max live loss are separate controls.
  - Global/default live-validation policy now exposes
    `max_supervised_live_loss_usd=10.0`.
  - Active validation lanes can still define different budgets/order sizing
    through strategy metadata; sampled active lanes show budgets from `18.0` to
    `30.0` USD while keeping `max_supervised_live_loss_usd=10.0`.
- Runtime/data checkpoint:
  - A/B/C/D health recovered to `ok` after a bounded B Profiles refresh.
  - The B profile distribution service was alive, but current runtime profile
    context had aged out; a constrained external fetch (`24` profiles,
    concurrency `3`) produced `48` current profile components and healthy live
    distribution snapshots.
- Live pipeline checkpoint:
  - Strategy queue worker drained two live-candidate rows after profile
    freshness recovered.
  - Promotion counts moved to `LIVE_RUNNING=4`, `LIVE_CANDIDATE=12`.
  - Since `2026-06-08T06:45:00Z`, `11` distinct strategies have exchange-backed
    live order submissions; `9` have fill rows and `2` remain submitted/unfilled
    live activity.
  - Latest filled live lane added:
    `master_hedge_grid_floor_tail_reversal_probe_v3` with one filled order and
    lifecycle/reconciliation passed.
- Resource/issue note:
  - The worker wrote its queue report and completed two actions, but the shell
    wrapper timed out before returning JSON. Treat this as a queue-worker CLI
    completion/reporting issue if it repeats; do not widen queue throughput
    while Postgres CPU is elevated.
- Tests:
  - `python -m pytest tests/crypto_options_app/test_strategy_promotion_manager_pytest.py tests/crypto_options_app/test_strategy_pipeline_queue_worker_pytest.py tests/crypto_options_app/test_strategy_manager_pytest.py -q` -> 38 passed.
- Live/manual-order status:
  - App-gated live execution remains enabled.
  - Codex manual orders remain prohibited and were avoided.

## 2026-06-08T08:20Z Budget/Loss Separation And Live-Lane Evidence Checkpoint

- Policy checkpoint:
  - Confirmed `budget != max allowed PnL loss`.
  - Strategy budgets/order sizing remain strategy-level metadata controls
    (`live_budget_cap_usd`, `live_target_order_notional_usd`,
    `live_min_order_notional_usd`, `live_min_order_shares`).
  - Runtime promotion policy and strategy promotion criteria now expose
    `max_supervised_live_loss_usd=10.0` as the hard per-strategy live loss stop
    unless a strategy explicitly defines a stricter value.
- Live pipeline checkpoint:
  - Since `2026-06-08T06:45:00Z`, `12` distinct strategies have exchange-backed
    live order submissions and `10` distinct strategies have filled live orders.
  - Latest filled lane:
    `profile_splus_hedger_follow_near50_hold_60s_v1` with one filled order,
    `5.04` USD notional.
  - A subsequent live-candidate drain reached the executor boundary for
    `option_tail_reversal_hold_60s_v3`, but blocked on
    `exchange_status_not_operational` / `polymarket_status_unavailable`.
    This is an external-status gate, not a Codex/user approval gate.
- Current mismatch to fix/track:
  - `strategy_promotion_state` currently reports fewer `LIVE_RUNNING` rows than
    the order/fill ledger evidence because the active-state classifier is
    narrower than "has exchange-backed submitted/filled live activity".
  - Frontend strategy/trading components should use both phase state and
    ledger-backed live activity counts so historical filled live lanes are not
    hidden as non-running.
- Health/resource note:
  - `/health` is degraded at this checkpoint due to
    `polymarket_status_timeout` and a stale `top_profiles_distribution`
    watermark, while Postgres remains the runtime backend and app live/order
    flags remain enabled.
  - Queue widening should wait for source freshness/status recovery; keep
    drains bounded.
- Tests:
  - `python -m pytest tests/crypto_options_app/test_strategy_promotion_manager_pytest.py tests/crypto_options_app/test_strategy_pipeline_queue_worker_pytest.py tests/crypto_options_app/test_strategy_manager_pytest.py -q` -> 38 passed.
- Live/manual-order status:
  - App-gated live execution remains enabled.
  - Codex manual orders remain prohibited and were avoided.

## 2026-06-08T08:30Z Ten Filled Live Lanes And Runtime Contract Reload

- Runtime policy reload:
  - Restarted only the `8011` FastAPI/uvicorn process so the running API matches
    the patched source contract.
  - `/strategies/promotion` now reports
    `budget_policy.max_supervised_live_loss_usd=10.0` and the strategy-defined
    criteria example also uses `10.0`.
  - App live/order flags remain enabled, while `manual_orders_allowed=false`
    and `manual_orders_avoided=true`.
- B Profiles recovery:
  - The profile distribution loop was not running; the status file was stale
    from `2026-06-06`.
  - Started a bounded profile distribution loop with external fetch enabled
    (`max_profiles=24`, `max_concurrency=3`, `timeout=5s`,
    `interval=30s`).
  - First observed loop status: `healthy`, `51` component rows inserted,
    `6` event snapshots, no blockers.
- Live lane checkpoint:
  - Since `2026-06-08T06:45:00Z`, `12` distinct strategies have exchange-backed
    live submissions and `10` distinct strategies have filled live orders.
  - Current validation ledger realized PnL for this fresh batch is still `0.0`
    because positions are open; no hard stop has triggered.
  - Filled notional/open-cost evidence is present for profile-follow,
    profile-path-active, S++ hedger, near-50, and master hedge-grid tail
    reversal lanes.
- Current decision:
  - Do not add more exposure solely to increase lane count: the requested
    `10` filled live-lane threshold has been met and frequency is adequate for
    a first validation batch.
  - Next action is to monitor exit/settlement accounting and let the app's
    per-strategy PnL/loss-streak/hard-loss gates block, demote, or keep lanes
    running.

## 2026-06-08T09:35Z Active 10+ Live Strategy Checkpoint

- Live-lane result:
  - Control-center state reached `11` current active live strategies and `11`
    active live positions at `2026-06-08T09:33:06Z`.
  - Active strategies:
    `master_hedge_grid_floor_tail_reversal_probe_v4`,
    `master_hedge_grid_floor_tail_reversal_probe_v5`,
    `master_hedge_grid_floor_tail_reversal_probe_v6`,
    `option_tail_reversal_hold_60s_v3`,
    `profile_splus_hedger_follow_hold_60s_v10`,
    `profile_splus_hedger_follow_hold_60s_v6`,
    `profile_splus_hedger_follow_hold_60s_v8`,
    `profile_splus_hedger_follow_near50_hold_60s_v1`,
    `profile_splus_hedger_follow_path_active_hold_60s_v1`,
    `profile_splus_hedger_follow_path_active_scalp_v1`,
    `profile_splusplus_hedger_follow_hold_60s_v4`.
  - Active live open cost was `$65.2912`; Codex manual orders were avoided.
- Runtime fixes:
  - Enabled verified-market fallback in the minimal live runner so transient
    Polymarket status JSON/page failures do not block a fresh verified CLOB
    quote.
  - Added `--max-parallel-live-candidates` to the strategy pipeline queue
    worker. This only parallelizes already-claimed `live_candidate` items;
    historical backtest and shadow replay remain sequential.
- Tests:
  - `python -m pytest tests/crypto_options_app/test_live_minimal_validator_pytest.py -q`
    -> 5 passed.
  - `python -m pytest tests/crypto_options_app/test_strategy_pipeline_queue_worker_pytest.py tests/crypto_options_app/test_live_minimal_validator_pytest.py -q`
    -> 13 passed.
- Remaining blockers:
  - Current-active count decays quickly because 5-minute event positions age
    out; maintaining 10 continuously requires the live-candidate worker to run
    on a tight cadence or as a dedicated app runtime loop.
  - Portfolio/control-center summary still reports validation ledger totals as
    zero even while active live fills are visible through `active_live`. Fix the
    dashboard portfolio aggregation before relying on root PnL/open-cost cards.
  - Expired open positions still need settlement/exit accounting validation so
    realized PnL and loss-stop gates are observable after event close.

## 2026-06-08T10:26Z Twelve Active Live Strategies And Dashboard Open-Cost Fix

- Live-lane result:
  - Forced a 10-strategy runtime batch through `run_minimal_live_validation_batch`
    using strategy-defined gates and per-strategy executor sizing; no Codex
    manual orders were placed.
  - First batch filled 7/10 live strategy orders; blocked rows were blocked by
    their own strategy gates (`option_entry_price_below_band`,
    `profile_distribution_group_thin`, or `fill_evidence_missing`).
  - Gap-fill batch filled 5/5 alternate live strategy orders.
  - Control-center verification after API restart reported:
    - `active_live_position_count=14`
    - `active_live_strategy_count=12`
    - `active_live_activity_strategy_count=13`
    - `active_live_open_cost_usd=79.6517`
    - `active_live_order_count=3`
  - Active filled-position strategies:
    `master_hedge_grid_floor_buy_only_accumulator_v1`,
    `master_hedge_grid_floor_tail_reversal_probe_v4`,
    `master_hedge_grid_floor_tail_reversal_probe_v6`,
    `option_tail_reversal_hold_60s_v3`,
    `profile_outcome_predictor_follow_hold_60s_v2`,
    `profile_outcome_predictor_follow_hold_60s_v3`,
    `profile_splus_hedger_follow_hold_60s_v10`,
    `profile_splus_hedger_follow_hold_60s_v6`,
    `profile_splus_hedger_follow_near50_hold_60s_v1`,
    `profile_splus_hedger_follow_path_active_hold_60s_v1`,
    `profile_splus_hedger_follow_path_active_scalp_v1`,
    `profile_splusplus_hedger_follow_hold_60s_v4`.
- Runtime/UI fixes:
  - Patched `/dashboard/control-center-state` so portfolio open cost and filled
    validation notional fall back to current active live exposure when the
    legacy validation budget ledger has no current rows.
  - Added active live order strategy reporting so the dashboard can distinguish
    filled positions from submitted live orders waiting for fill evidence.
  - Restarted only the FastAPI/uvicorn process on `8011` to load the patched
    dashboard code; Postgres/live order state remained intact.
- Tests:
  - `python -m pytest tests/crypto_options_app/test_live_minimal_validator_pytest.py tests/crypto_options_app/test_strategy_pipeline_queue_worker_pytest.py -q`
    -> 13 passed.
- Remaining blockers:
  - `validation_budget_ledger` still has no current rows for the live batch, so
    current realized PnL is not yet represented in the dashboard ledger.
  - The next backend fix is settlement/exit accounting for current live
    positions so per-strategy `$10` max-loss stops, win rate, realized PnL, and
    demotion gates can be audited after event close.


## 2026-06-08T10:43:47Z Live Lane Collateral Hard Block

- Requested state: maintain 10 live strategy lanes with strategy-level budgets/order sizing and a separate `$10` max-live-loss cap per strategy.
- Action taken: reran focused live-validator tests, patched strategy live validation controls to include `live_price_slippage_cents`, patched runtime attribution to persist the live executor response payload, and launched a fresh 10-strategy live refill batch through the app runtime.
- Result: only `master_hedge_grid_floor_tail_reversal_probe_v6` filled in the latest batch. The other nine strategies reached live execution but were blocked by `clob_collateral_balance_too_low` after asset checks.
- Wallet/runtime observation: CLOB available collateral dropped from about `$4.45` to about `$0.22`; `view_orders(open_only=True)` returned `0` open orders, while `view_open_positions(min_size=0.01)` returned `77` positions, many recent 5-minute positions with current value `$0.00` and negative cash PnL.
- Current blocker: not enough available CLOB collateral to run 10 live lanes. This is not a Codex approval gate and not a queue gating issue.
- Safety status: no Codex manual orders; app live runtime submitted strategy-created orders only. Further live submissions should pause until collateral/reconciliation/loss-stop state is repaired or the wallet is funded/redeemed.
- Tests: `python -m pytest tests/crypto_options_app/test_live_minimal_validator_pytest.py tests/crypto_options_app/test_strategy_pipeline_queue_worker_pytest.py -q` -> 13 passed.
- Next backend action: audit settlement/reconciliation and per-strategy live loss stop enforcement so expired losing positions demote strategies and the UI shows account collateral/open-position drawdown before any new refill attempt.

## 2026-06-08T11:43Z Live Loss Stop Incident Containment

- Incident: Polymarket trade history and account/position audit showed real losses while the app `validation_budget_ledger` kept recent live `realized_pnl_usd` at `$0.00`.
- Root blocker: live loss-stop and dashboard reporting were reading incomplete app ledger state rather than account-authoritative settlement/position PnL.
- Immediate containment:
  - `orders_allowed` default changed to `false`.
  - `live_trading_authorized` default changed to `false`.
  - API restarted; `/health` reports both live/order flags as `false`.
  - `14` queued `live_candidate` queue items blocked with `incident_block:live_loss_stop_accounting_mismatch`.
  - `15` `LIVE_CANDIDATE`/`LIVE_RUNNING` strategy states moved to `REVIEW_BLOCKED`.
- Manual orders: none placed by Codex.
- Incident report: `crypto_options_app/artifacts/team_coordination/incident_live_loss_stop_20260608.md`.
- Required next action: do not submit new live orders until settlement/reconciliation updates `validation_budget_ledger`, strategy loss stops are tested from actual account PnL, and event/account exposure caps are enforced.

## 2026-06-08T13:03Z Replay/Reconciliation Recovery Scope

- Created local recovery plan:
  `crypto_options_app/artifacts/team_coordination/incident_replay_reconciliation_recovery_plan_20260608.md`.
- Created GitHub blocker issues:
  - [#172 Calibrate replay/backtest engine against 2026-06-08 live loss window](https://github.com/LucaCGN/janus_cortex/issues/172)
  - [#173 Account-authoritative PnL reconciliation and live loss stops](https://github.com/LucaCGN/janus_cortex/issues/173)
- Existing issue [#168](https://github.com/LucaCGN/janus_cortex/issues/168) was linked to both blockers.
- Root replay finding:
  current `simulate_fill` evaluates one quote frame at decision time. It does not yet model path movement after simulated latency, TTL, partial/unfilled limit behavior, slippage/depth over time, or settlement outcome writeback.
- Root reconciliation finding:
  Polymarket winning positions can auto-redeem, while losing positions can remain visible as `$0.00`; both must become account-authoritative app PnL so loss stops and demotions use the real account state.
- Current live policy:
  live trading remains disabled and all live resumption is blocked until #172 and #173 have focused implementation tests and a passing incident calibration report.

## 2026-06-08T13:10Z Replay Path-Fill Primitive

- Added `simulate_fill_path` and `PathFillSimulationConfig` in
  `crypto_options_app/replay/fill_simulation.py`.
- The existing single-frame `simulate_fill` remains unchanged for current
  callers.
- New replay primitive models decision-to-submit latency, TTL, missed limit
  orders, post-latency market fills, depth-limited partial fills, and unfilled
  results.
- Focused tests passed:
  - `python -m pytest tests/crypto_options_app/test_replay_fill_path_simulation_pytest.py -q`
    -> 3 passed.
  - `python -m pytest tests/crypto_options_app/test_replay_engine_pytest.py tests/crypto_options_app/test_runtime_adapter_pytest.py::test_runtime_adapter_shadow_uses_fill_simulation_for_partial_fill_pytest tests/crypto_options_app/test_replay_fill_path_simulation_pytest.py -q`
    -> 9 passed.
- Next: wire this primitive into an incident calibration harness for #172; do
  not resume live strategy testing before #172 and #173 pass.

## 2026-06-08T13:15Z Incident Replay Calibration Baseline

- Added read-only CLI:
  `python -m crypto_options_app.scripts.run_crypto_options_incident_replay_calibration`.
- The CLI loads actual incident live decisions from Postgres, resolves synthetic
  token keys through `event_tokens`, replays captured quote paths with
  `simulate_fill_path`, and settles against the incident outcome map.
- Output artifacts:
  - `crypto_options_app/artifacts/reports/incident_replay_calibration_latest.md`
  - `crypto_options_app/artifacts/reports/incident_replay_calibration_ttl60_latest.md`
- First results:
  - `500ms` latency / `5s` TTL: `67` decisions, `9` filled, `58` unfilled.
  - `500ms` latency / `60s` TTL: `67` decisions, `52` filled, `15` unfilled.
- Important finding:
  C quote-path sampling is too sparse for literal five-second TTL calibration,
  but the 60-second diagnostic replay is already directionally useful.
- The 60-second diagnostic replay reproduces the core live/backtest false
  positives:
  - V10 actual `-$47.3250`, calibrated `-$43.0000`.
  - V6 actual `-$39.5602`, calibrated `-$27.0720`.
  - near50 actual `-$35.4188`, calibrated `-$27.7580`.
  - ETH Down cluster actual `-$79.6517`, calibrated `-$65.7055`.
- It also keeps V5 tail reversal positive but reduced:
  actual `+$10.7216`, calibrated `+$5.2700`.
- This is not live authorization. It is the first replay-calibration baseline
  for #172.

## 2026-06-08T13:58Z Replay/PnL Incident Fix Checkpoint

- Live trading remains disabled; no Codex manual orders were placed.
- Replay calibration is now wired into strategy promotion scoring through
  `crypto_options_app/replay/calibration_report.py` and
  `crypto_options_app/strategies/promotion.py`.
- The calibration layer uses measured executor latency where available and
  keeps quote-path data quality as a separate blocker. `500ms` is not treated
  as a universal fix because the incident path data shows sub-second through
  multi-second latency and sparse quote cadence.
- Latest promotion-scoring calibration report:
  `crypto_options_app/artifacts/reports/strategy_replay_calibration_latest.md`.
- Account-authoritative settlement writeback is implemented in
  `crypto_options_app/trading/account_reconciliation.py` with dry-run CLI:
  `python -m crypto_options_app.scripts.run_crypto_options_account_reconciliation --dry-run --min-open-size 0`.
- Latest dry run fetched `76` open positions and `50` closed positions, mapped
  `64` local settlements, and reported `0` missing ledgers without writing.
- Aggregate closed/redeemed Polymarket PnL is now allocated pro rata across
  local strategy positions so a single account-level result cannot be copied to
  every strategy lane.
- Focused tests:
  `python -m pytest tests/crypto_options_app/test_account_reconciliation_pytest.py tests/crypto_options_app/test_replay_fill_path_simulation_pytest.py tests/crypto_options_app/test_strategy_promotion_manager_pytest.py::test_strategy_promotion_blocks_scoring_ready_negative_calibrated_replay_pytest -q`
  -> 8 passed.
- Current controlled retest candidates, not live-promoted:
  `master_hedge_grid_floor_tail_reversal_probe_v5`,
  `option_tail_reversal_hold_60s_v3`,
  `profile_outcome_predictor_follow_hold_60s_v3`, with
  `profile_splus_outcome_prediction_confluence_v1` as a caution candidate due
  lower quote-path quality.
- Remaining closure condition: apply account reconciliation in writeback mode
  during a controlled validation window, then run 1-3 `$10` loss-cap candidates
  and confirm real account wins/losses drive dashboard PnL, stop gates, and
  demotion.

## 2026-06-08T15:25Z Three-Slot Live Boundary And Queue Backfill Fix

- Live testing policy is now explicitly capped at `3` active strategy lanes.
- Patched `strategy_pipeline_queue_worker` defaults to claim `3` items, run up to
  `3` live candidates in parallel, and enforce `max_live_strategy_slots=3`.
- Added global live-slot accounting from active live positions plus active live
  orders. Extra `LIVE_CANDIDATE` queue items remain queued with
  `live_strategy_slot_capacity_reached`, and the same strategy is requeued for
  `recent_shadow_sample` so it keeps shadow-trading while waiting for a live
  slot.
- Patched the compatibility scheduler and CLI defaults to use the same `3` slot
  boundary.
- Current state check after the patch:
  - active live positions: `0`
  - active live orders: `0`
  - promotion counts included `6 LIVE_CANDIDATE`, `4 SHADOW_READY`, `68 SHADOW_REVIEW`
  - queue active phase only had `7 recent_shadow_sample` rows, exposing that the
    paused automation command was using `--no-backfill-from-promotions`.
- Updated the paused queue-worker automation prompt to allow promotion-state
  backfill and to use:
  `$env:JANUS_CRYPTO_OPTIONS_ORDERS_ALLOWED='true'; $env:JANUS_CRYPTO_OPTIONS_LIVE_TRADING_AUTHORIZED='true'; python -m crypto_options_app.scripts.run_crypto_options_strategy_pipeline_queue_worker --max-items 3 --max-parallel-live-candidates 3 --max-live-strategy-slots 3 --max-scenarios-per-action 1 --max-trades-per-strategy 1 --validation-budget-cap-usd 10 --recent-shadow-retry-seconds 300`.
- The current interactive shell has live/order flags unset, so direct shell runs
  here validate without live submission unless those two env vars are set for
  that process.
- Focused tests passed:
  `python -m pytest tests\crypto_options_app\test_strategy_pipeline_queue_worker_pytest.py tests\crypto_options_app\test_strategy_pipeline_scheduler_pytest.py -q`
  -> `16 passed`.
- No Codex manual orders were placed in this patch pass.

## 2026-06-08T16:36Z First Controlled Live Order Reconciliation Checkpoint

- Activated the strategy pipeline queue worker for one bounded live-candidate pass
  with app live/order flags scoped to that worker process.
- First automated app-managed live order:
  `master_hedge_grid_floor_tail_reversal_probe_v5` on
  `eth-updown-5m-1780935300:down`.
- Exchange order:
  `0x0864709c400575493a21c98b222b4bfb1af68ccb343929c7d204d24c6561d531`.
- Fill evidence:
  local fill `45.0` shares at `0.08`; Polymarket UI showed Down `9c`
  and approximately `-$3.83`.
- Account Data API reported the position as a losing open position with
  `current_value=0` and `cash_pnl=-3.60`, matching the expected Polymarket
  behavior where losing positions remain visible until redemption.
- Patched account reconciliation Postgres compatibility:
  SQLite-style `MAX(0, value)` in writeback was replaced with a portable
  `CASE` expression.
- Patched account reconciliation run-id extraction:
  `_validation_run_id_from_order_json` now prefers executor `app_run_id` and
  right-splits candidate keys so strategy-scoped live run ids are preserved.
- Targeted repair applied to the first live order ledger:
  realized PnL `-3.60`, open cost `0`, reconciliation `reconciled`.
- Promotion evaluator moved
  `master_hedge_grid_floor_tail_reversal_probe_v5` to `SHADOW_REVIEW` with
  `live_shadow_actual_drift_exceeds_limit`.
- Queue worker automation was paused again pending the next fix because the
  live-candidate path records a `cashout_watch` plan but does not submit a
  corresponding exit/hedge order. That is not safe for strategies requiring
  active managed exits.
- GitHub source of truth: issue `#173` updated with the checkpoint.
- Focused tests:
  `python -m pytest tests\crypto_options_app\test_account_reconciliation_pytest.py -q`
  -> `6 passed`.
- Focused queue tests:
  `python -m pytest tests\crypto_options_app\test_strategy_pipeline_queue_worker_pytest.py tests\crypto_options_app\test_strategy_pipeline_scheduler_pytest.py -q`
  -> `16 passed`.

## 2026-06-08T16:53Z Live Runtime Paired Exit Invariant

- Patched the live runtime so any live BUY fill now creates and submits a
  paired same-token `limit_sell` immediately.
- A passive `cashout_watch`/planned-only managed exit no longer counts as
  healthy live lifecycle coverage for a filled live position.
- The paired exit is persisted as first-class lifecycle state:
  `execution_intents` side `SELL`, `orders` with the sell order key, and
  `exit_orders.order_key` linked to that order.
- The Polymarket executor now forces paired-exit limit sells to `GTC`, even if
  the entry executor config uses `FOK`, so the sell can rest instead of failing
  as a non-resting cashout attempt.
- If the paired sell is rejected/unsubmitted or has reconciliation blockers, the
  live validation blocks the strategy instead of counting it as covered.
- Queue worker remains paused until the next controlled validation pass.
- Focused tests:
  `python -m pytest tests\crypto_options_app\test_runtime_adapter_pytest.py::test_runtime_adapter_live_blocks_when_paired_exit_sell_is_not_submitted_pytest tests\crypto_options_app\test_runtime_persistence_pytest.py::test_runtime_persistence_links_live_paired_exit_order_pytest tests\crypto_options_app\test_polymarket_supervised_executor_pytest.py::test_polymarket_supervised_executor_uses_gtc_for_paired_exit_sell_pytest -q`
  -> `3 passed`.
- No Codex manual orders were placed in this patch pass.

## 2026-06-08T17:26Z Initial Live Lane Timing And Paired Exit Retry Checkpoint

- Kept the strategy pipeline queue worker paused while correcting the first
  controlled paired-exit failure.
- The first `option_tail_reversal_hold_60s_v3` order was routed through app
  runtime, but this tail/underdog style is not suitable for initial live
  validation. Tail/underdog strategies are now excluded from the initial live
  validation lane unless explicitly opted in by strategy metadata.
- Added an event-open timing gate for initial live validation candidates:
  current events must still be inside the configured opening window
  (`max_seconds_since_event_start`, default `90s`) and must satisfy the existing
  minimum time-remaining guard.
- Patched paired-exit execution to retry bounded GTC sell submission when the
  CLOB conditional-token balance lags immediately after a filled BUY
  (`clob_conditional_balance_too_low`).
- Refreshed promotion state after the patch:
  `option_tail_reversal_hold_60s_v3` moved from stale `LIVE_RUNNING`/candidate
  state to `SHADOW_REVIEW`; active live orders and active live positions are
  both `0`.
- Current promotion counts after refresh:
  `3 LIVE_CANDIDATE`, `5 SHADOW_READY`, `66 SHADOW_REVIEW`, `42 REVIEW_BLOCKED`.
- Focused tests:
  `python -m pytest tests\crypto_options_app\test_runtime_adapter_pytest.py::test_runtime_adapter_live_retries_paired_exit_until_conditional_balance_is_visible_pytest tests\crypto_options_app\test_runtime_adapter_pytest.py::test_runtime_adapter_live_blocks_when_paired_exit_sell_is_not_submitted_pytest tests\crypto_options_app\test_runtime_persistence_pytest.py::test_runtime_persistence_links_live_paired_exit_order_pytest tests\crypto_options_app\test_polymarket_supervised_executor_pytest.py::test_polymarket_supervised_executor_uses_gtc_for_paired_exit_sell_pytest tests\crypto_options_app\test_core_flow_live_runner_pytest.py::test_core_flow_live_target_selection_rejects_late_initial_validation_entries_pytest tests\crypto_options_app\test_core_flow_live_runner_pytest.py::test_core_flow_live_initial_strategy_filter_excludes_tail_underdog_validation_lane_pytest tests\crypto_options_app\test_strategy_pipeline_scheduler_pytest.py::test_strategy_pipeline_live_candidate_blocks_tail_underdog_initial_validation_pytest tests\crypto_options_app\test_strategy_promotion_manager_pytest.py::test_strategy_promotion_routes_tail_underdog_initial_live_candidate_to_shadow_review_pytest -q`
  -> `8 passed`.
- App health check remained `ok`; A/B/C/D freshness was green.
- Read-only live-candidate probe found an eligible non-tail candidate
  (`eth-updown-5m-1780939800:down`) inside the opening window, but no live run
  was started after the probe because the candidate aged past the `90s` opening
  cutoff during verification. Next controlled attempt should wait for the next
  event open instead of chasing the same market late.
- No Codex manual orders were placed in this patch pass.

## 2026-06-08T17:58Z Strategy Pipeline Worker Active And Live Paired Sell Verified

- Reactivated the local `crypto-options-strategy-pipeline-queue-worker`
  automation and kept it active.
- Patched live-candidate queue semantics: current-market entry blockers now
  keep a strategy queued for retry instead of terminal-blocking it. Mechanical
  live/runtime blockers still block.
- Patched retryable market-context blockers to include paired-seed live-entry
  gates, so paired-seed candidates do not churn into terminal blocked state
  just because the current event is not suitable.
- Increased the active queue-worker automation command to `--max-items 6` so
  live candidates do not starve shadow/backtest queue work.
- Foreground worker verification:
  - `strategy-pipeline-queue-worker-20260608T174933Z`: `6` claimed, `2`
    actions executed.
  - `strategy-pipeline-queue-worker-20260608T175709Z`: `6` claimed, `3`
    actions executed, live submission attempted.
- Live order validation:
  - Strategy: `profile_outcome_predictor_follow_hold_60s_v3`
  - Entry BUY: filled `10.0` shares at `0.50`
  - Paired exit SELL: submitted as `paired_exit` `limit_sell` for `10.0`
    shares at `0.51`
  - Active live state after run: `1` live position, `1` active live order, open
    cost `$5.00`.
- App health remained `ok`; A/B/C/D freshness remained `ok`; runtime DB remained
  Postgres.
- Focused tests:
  `python -m pytest tests\crypto_options_app\test_strategy_pipeline_queue_worker_pytest.py::test_strategy_pipeline_queue_worker_requeues_live_candidate_market_context_blockers_pytest -q`
  -> `1 passed`.
- No Codex manual orders were placed.

## 2026-06-08T18:22Z Live Paired Exit Reconciled And Worker Still Active

- Monitored the active live validation lane after the paired-exit runtime patch.
- CLOB status check showed the paired exit order for
  `profile_outcome_predictor_follow_hold_60s_v3` was `MATCHED`, not still open:
  - Entry BUY: `10.0` shares at `0.50`
  - Paired exit SELL: `10.0` shares at `0.51`
  - Local realized PnL repaired to `+$0.10`
  - Local open cost repaired to `$0.00`
  - Active live orders: `0`
  - Active live positions: `0`
- Cleared `13` older stale exchange-backed local `submitted` orders after
  verifying they were not in CLOB open orders and had no matching trade-history
  entries; they were marked `expired`, with no PnL or position changes.
- Health rechecked after a short degraded watermark cycle; A/B/C/D freshness
  returned to `ok`.
- Ran one bounded queue-worker pass with the active automation command:
  `strategy-pipeline-queue-worker-20260608T181955Z`, `6` claimed, `2` executed,
  live submission attempted `False`, manual orders avoided `True`.
- Current queue behavior is correct: unsuitable live candidates remain queued for
  retry on market-context blockers; recent-shadow samples continue advancing.
- Focused tests:
  `python -m pytest tests\crypto_options_app\test_runtime_adapter_pytest.py::test_runtime_adapter_live_retries_paired_exit_until_conditional_balance_is_visible_pytest tests\crypto_options_app\test_core_flow_live_runner_pytest.py::test_core_flow_live_target_selection_rejects_late_initial_validation_entries_pytest tests\crypto_options_app\test_strategy_pipeline_queue_worker_pytest.py::test_strategy_pipeline_queue_worker_requeues_live_candidate_market_context_blockers_pytest -q`
  -> `3 passed`.
- No Codex manual orders were placed.

### 2026-06-08T18:54:16Z - Live queue/reconciliation stability pass

- Patched pending CLOB order reconciliation into ccount_reconciliation and the strategy pipeline queue worker post-live hook.
- Focused tests passed: 	ests/crypto_options_app/test_account_reconciliation_pytest.py, paired-exit runtime retry test, and live-candidate market-context requeue test (10 passed).
- Real account reconciliation completed with   open CLOB orders, 2077 trades fetched,   pending local exchange orders, and no manual orders.
- Queue worker remains active and bounded. Latest manual pass had 3 LIVE_CANDIDATE rows but   live submissions because current market context failed strategy-defined gates: profile_distribution_top_profile_concentration_high for confluence, and low hedge-grid viability/inversion/tail-reversal/path-density for paired seed V5/V6.
- Stability gate is not met yet:   active live orders/positions and no set of 3 positive live-running lanes. Candidate substitution exists in SHADOW_READY/DEMOTED_TO_SHADOW states, but live promotion is currently constrained by either strategy gates or prior incident demotion.
- Next action: keep worker active; do not force bad market contexts. Continue retrying bounded live candidates as market windows change, and only add the 30m monitor automation after 3 live lanes are actually active/positive or after user approves a monitor that watches for that condition.
## 2026-06-08T23:10Z Remote Postgres Cutover

- Verified remote Postgres accessibility at `192.168.0.156:5432` after the
  existing ZimaOS database was updated to include the `admin` role and
  `janus-postgres` database.
- Stopped the API and A/B/C writers, dumped the local Docker Postgres database,
  and restored it into remote `janus-postgres`.
- Exact table-count parity passed: `78` local public tables, `78` remote public
  tables, `0` mismatches.
- Added `JANUS_CRYPTO_OPTIONS_DATABASE_BACKEND=postgres`,
  `JANUS_CRYPTO_OPTIONS_POSTGRES_URL`, and
  `JANUS_CRYPTO_OPTIONS_POSTGRES_ENABLED=true` to the root `.env`.
- Patched Postgres URL parsing to URL-decode username/password fields, required
  for the remote password characters.
- Restarted the API and A/B/C data services against remote Postgres.
- Health endpoint is `ok`, schema is `complete`, and fresh data-service writes
  are landing in the remote database.
- Local Docker Postgres remains running as rollback; do not delete it until the
  next strategy/reconciliation validation pass confirms remote stability.
- Focused parser test passed:
  `python -m pytest tests\crypto_options_app\test_postgres_foundation_pytest.py::test_postgres_settings_parse_url_encoded_credentials -q`.
- No Codex manual orders were placed.

## 2026-06-08T23:25Z Docs/GitHub Source-Of-Truth Review

- Reviewed active runtime, storage, promotion, fixed-chat, frontend, and GitHub
  coordination docs.
- Added live/replay/trading handoff:
  `crypto_options_app/artifacts/team_coordination/live_engine_strategy_handoff_20260608.md`.
- Added review artifact:
  `crypto_options_app/artifacts/team_coordination/docs_github_review_20260608.md`.
- GitHub updated:
  - P7 milestone renamed to `CRYPTO-P7 Policy-Gated Live Readiness`.
  - #165 renamed to policy-gated live promotion preflight.
  - #172 and #173 assigned to P7.
  - comments posted on #151, #152, #168, #172, and #173.
- Active docs now treat `supervised_live` wording as historical/storage
  compatibility only, not a live approval model.
- No Codex manual orders were placed.
