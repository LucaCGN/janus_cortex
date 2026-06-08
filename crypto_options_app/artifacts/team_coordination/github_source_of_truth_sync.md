# GitHub Source Of Truth Sync

Updated: 2026-06-08T23:25:00Z

Repository: `LucaCGN/janus_cortex`

## Status

- Labels created: `crypto-options`, `transition`, `db-data`, `signals`, `strategies`, `frontend`, `promotion-policy`, `safety`, `repo-cleanup`, `blocked`, `ready-for-fixed-chat`.
- Milestones created: `CRYPTO-P4 Transition Control Plane`, `CRYPTO-P5 Signal And Strategy Queue Trust`, `CRYPTO-P6 Frontend Control Center`, `CRYPTO-P7 Policy-Gated Live Readiness`.
- Issues created: `20`.
- Draft PR created: [#170 Crypto options compatibility wrapper cutover](https://github.com/LucaCGN/janus_cortex/pull/170).
- Runtime DB adapter audit: 0 production runtime direct SQLite blockers, 0 review-required SQLite usages, 11 allowed migration/test/compat usages; legacy profile/market SQLite stores are fenced to research compatibility CLIs and tracked under issue [#152](https://github.com/LucaCGN/janus_cortex/issues/152).
- Redis hot-plane gate: adapter tests now cover JSON TTL cache, NX/EX owner locks, owner-checked release, and RESP command framing; Redis remains disabled until a measured use case is selected, tracked under issue [#151](https://github.com/LucaCGN/janus_cortex/issues/151).
- Promotion/demotion policy rendering: strategy promotion summaries expose `crypto_options_promotion_policy_contract_v1`, including non-promotable signal labels, strict replay gate, default 12+ distinct recent samples, default >70% win rate, positive PnL, lifecycle/reconciliation, drift/demotion blockers, and strategy-defined criteria from `metadata.promotion_policy`, `metadata.promotion_criteria`, `risk_gates.promotion_policy`, or `live_pulse_requirements.promotion_policy`; tracked under issue [#153](https://github.com/LucaCGN/janus_cortex/issues/153).
- Strategy criteria schema follow-up: the current map-based criteria surface is backward-compatible, but a dedicated `StrategySpec.promotion_criteria` field is now tracked as a contract follow-up under [#153](https://github.com/LucaCGN/janus_cortex/issues/153), [#165](https://github.com/LucaCGN/janus_cortex/issues/165), and [#166](https://github.com/LucaCGN/janus_cortex/issues/166).
- GitHub comments posted for this contract change: #153 comment `4642857459`, #165 comment `4642857500`, #166 comment `4642857532`.
- Frontend fixed chat: ready.
- Signal/strategy cleanup fixed chat: ready to start from GitHub issue source-of-truth.

## Milestones

- `CRYPTO-P4 Transition Control Plane`: 5 open issues.
- `CRYPTO-P5 Signal And Strategy Queue Trust`: 5 open issues.
- `CRYPTO-P6 Frontend Control Center`: 5 open issues.
- `CRYPTO-P7 Policy-Gated Live Readiness`: 7 open issues.

## Issues

### CRYPTO-P4 Transition Control Plane

- [#150 [CRYPTO-P4-01] Team coordination artifacts and fixed-chat handoff contracts](https://github.com/LucaCGN/janus_cortex/issues/150)
- [#151 [CRYPTO-P4-02] Storage architecture audit and Redis hot-plane gate](https://github.com/LucaCGN/janus_cortex/issues/151)
- [#152 [CRYPTO-P4-03] Runtime DB adapter audit and SQLite production-path removal](https://github.com/LucaCGN/janus_cortex/issues/152)
- [#153 [CRYPTO-P4-04] Promotion/demotion policy rendering and enforcement](https://github.com/LucaCGN/janus_cortex/issues/153)
- [#154 [CRYPTO-P4-05] Repo cleanup inventory and reference-root move plan](https://github.com/LucaCGN/janus_cortex/issues/154)

### CRYPTO-P5 Signal And Strategy Queue Trust

- [#155 [CRYPTO-P5-01] Signal strict replay state cleanup](https://github.com/LucaCGN/janus_cortex/issues/155)
- [#156 [CRYPTO-P5-02] Strategy promotion state cleanup](https://github.com/LucaCGN/janus_cortex/issues/156)
- [#157 [CRYPTO-P5-03] Profile-follow candidate evidence accumulation](https://github.com/LucaCGN/janus_cortex/issues/157)
- [#158 [CRYPTO-P5-04] Hedge-grid protected-floor selector readiness](https://github.com/LucaCGN/janus_cortex/issues/158)
- [#159 [CRYPTO-P5-05] Shadow/live drift reporting and blocker taxonomy](https://github.com/LucaCGN/janus_cortex/issues/159)

### CRYPTO-P6 Frontend Control Center

- [#160 [CRYPTO-P6-01] Root operating dashboard flow](https://github.com/LucaCGN/janus_cortex/issues/160)
- [#161 [CRYPTO-P6-02] Source and indicator health views](https://github.com/LucaCGN/janus_cortex/issues/161)
- [#162 [CRYPTO-P6-03] Signal backtest and live-shadow views](https://github.com/LucaCGN/janus_cortex/issues/162)
- [#163 [CRYPTO-P6-04] Strategy backtest, shadow, live, and promotion views](https://github.com/LucaCGN/janus_cortex/issues/163)
- [#164 [CRYPTO-P6-05] Portfolio, positions, open orders, history, event, and profile views](https://github.com/LucaCGN/janus_cortex/issues/164)

### CRYPTO-P7 Policy-Gated Live Readiness

- [#165 [CRYPTO-P7-01] Policy-gated live promotion preflight](https://github.com/LucaCGN/janus_cortex/issues/165)
- [#166 [CRYPTO-P7-02] Live demotion and stop-gate enforcement](https://github.com/LucaCGN/janus_cortex/issues/166)
- [#167 [CRYPTO-P7-03] Budget scaling and descaling policy](https://github.com/LucaCGN/janus_cortex/issues/167)
- [#168 [CRYPTO-P7-04] Live reconciliation and shadow/live drift audit](https://github.com/LucaCGN/janus_cortex/issues/168)
- [#169 [CRYPTO-P7-05] First live candidate readiness report](https://github.com/LucaCGN/janus_cortex/issues/169)

## Fixed Chat Start Points

- Frontend Control Center Developer: `crypto_options_app/artifacts/team_coordination/fixed_chat_prompts/frontend_control_center_developer.md`, primarily issues #160-#164.
- Signal And Strategy Management Cleanup: `crypto_options_app/artifacts/team_coordination/fixed_chat_prompts/signal_strategy_management_cleanup.md`, primarily issues #155-#159.

## 2026-06-07T14:35Z Frontend/Runtime State Sync

- Issue #160: root operating dashboard now shows supervised testing/order capability from current runtime flags instead of stale `Gated read only` / `orders disabled` cache values.
- Issue #164: Portfolio current metrics now exclude stale historical supervised-test artifacts; old `-$8.01` realized PnL and `$2.09` open cost remain visible as historical excluded evidence, and event/profile panels now prefer usable chart/profile rows.
- Issue #163: Strategy runtime rows from stale stopped supervised tests now render as `HISTORICAL EXECUTED`, not active live exposure.
- Issue #168: remaining unresolved historical closed-event position is logged in `technical_issue_handoff_log.md` and `handoff_queue.jsonl` for reconciliation cleanup/annotation.
- Browser verification passed all control tabs on `http://127.0.0.1:8011/v1/crypto-options-app` with no endpoint-load errors.
- GitHub comments posted for this sweep: #160 comment `4642963738`, #164 comment `4642964241`, #163 comment `4642964862`, #168 comment `4642966002`.

## 2026-06-07T17:15Z Spark Signal-Review Integrity Gate

- Issue #155: signal catalog item retrieval is fixed for Spark signal-review cleanup; `/signals/catalog/{signal_id}` no longer returns 500 for the checked V4 row, and cleanup batch usage now excludes completed handoff rows with `--exclude-done-handoff`.
- Issue #163: frontend/control surface now reports signal and strategy status breakdowns: `57` promotion-ready signals, `115` revision candidates, `12` shadow-ready strategies, and `0` live-running strategies.
- GitHub comments posted for this gate: #155 comment `4643376232`, #163 comment `4643376271`.

## 2026-06-07T23:07Z Strategy Pipeline Queue Fairness

- New GitHub issue: [#171 Strategy pipeline queue can starve backtests behind recurring recent-shadow samples](https://github.com/LucaCGN/janus_cortex/issues/171).
- Local fix:
  - recurring `recent_shadow_sample` items receive a claim-order fairness penalty after their first attempt;
  - phase ordering now prefers historical backtest, then shadow replay, then recent-shadow sampling, then live candidate when effective priority ties.
- Validation:
  - `python -m pytest tests/crypto_options_app/test_strategy_pipeline_queue_worker_pytest.py -q` -> 5 passed.
  - next backend claim-order check selects `historical_backtest:profile_splus_hedger_follow_hold_60s_v11:v11`, not the recurring V16 sample.
- Recovery note: after the internet/provider interruption, API and source services recovered, but Docker Postgres was still hot during inspection; keep the queue worker under its resource guard before widening throughput.

## 2026-06-07T23:19Z Empty-Selector Backtest Queue Blocker

- Issue [#171](https://github.com/LucaCGN/janus_cortex/issues/171) comment `4644398710`.
- The queue fairness fix was validated against live state: the worker claimed `historical_backtest:profile_splus_hedger_follow_hold_60s_v11:v11`.
- Follow-up local fix:
  - historical backtest now returns a structured `scenario_selector:no_matching_scenarios` payload when a selector has no rows;
  - zero-scenario blocker payloads become blocked queue actions rather than false `executed/done` work.
- Validation:
  - `python -m pytest tests/crypto_options_app/test_strategy_live_replay_worker_pytest.py::test_strategy_backtest_replay_worker_handles_empty_selector_without_crash_pytest tests/crypto_options_app/test_strategy_pipeline_queue_worker_pytest.py -q` -> 7 passed.
  - UI now shows `120 registered; 60 queued, 0 running, 14 shadow-ready, 0 live-running`.
  - next queue claim target is `historical_backtest:profile_splus_hedger_follow_hold_60s_v12:v12`.

## 2026-06-08T13:03Z Live-Loss Incident Recovery Issues

- New GitHub issue: [#172 Calibrate replay/backtest engine against 2026-06-08 live loss window](https://github.com/LucaCGN/janus_cortex/issues/172).
- New GitHub issue: [#173 Account-authoritative PnL reconciliation and live loss stops](https://github.com/LucaCGN/janus_cortex/issues/173).
- Issue [#168](https://github.com/LucaCGN/janus_cortex/issues/168) comment `4649247074` links the new blocker issues back to the existing reconciliation/drift audit.
- Local recovery plan: `crypto_options_app/artifacts/team_coordination/incident_replay_reconciliation_recovery_plan_20260608.md`.
- Live strategy resumption remains blocked until:
  - replay calibration can reproduce or explain the 2026-06-08 live/backtest divergence;
  - winning auto-redeems and losing `$0.00` positions write account-authoritative PnL back to the app risk surface;
  - per-strategy, account, event, and outcome exposure stops are tested from account-authoritative state.

## Safety

GitHub issues do not authorize Codex manual orders or gate bypasses. Live execution remains gated by strategy criteria, promotion policy, executor boundary, budget/risk, reconciliation, lifecycle coverage, stop gates, and explicit runtime authorization.

## 2026-06-08T13:58Z #172/#173 Implementation Checkpoint

- [#172](https://github.com/LucaCGN/janus_cortex/issues/172):
  - implemented measured-latency/path-fill incident calibration;
  - added quote-path data-quality scoring;
  - generated `crypto_options_app/artifacts/reports/strategy_replay_calibration_latest.md`;
  - wired calibration blockers into strategy promotion scoring.
- [#173](https://github.com/LucaCGN/janus_cortex/issues/173):
  - implemented account-authoritative settlement mapping/writeback module;
  - added Polymarket open losing `$0.00` position handling;
  - added closed/redeemed aggregate PnL allocation across local strategy positions;
  - added dry-run account reconciliation CLI.
- Latest dry run:
  - `76` open account positions fetched;
  - `50` closed account positions fetched;
  - `64` local settlements mapped;
  - `0` missing ledgers;
  - no writes because dry-run mode was used.
- Focused tests:
  - `python -m pytest tests/crypto_options_app/test_account_reconciliation_pytest.py tests/crypto_options_app/test_replay_fill_path_simulation_pytest.py tests/crypto_options_app/test_strategy_promotion_manager_pytest.py::test_strategy_promotion_blocks_scoring_ready_negative_calibrated_replay_pytest -q`
  - result: 8 passed.
- Live status:
  - live trading remains disabled;
  - no Codex manual orders were placed;
  - remaining closure is controlled writeback/live validation, not more broad strategy promotion.

## 2026-06-08T23:25Z Remote Postgres And Live Runtime Handoff

- P7 milestone renamed to `CRYPTO-P7 Policy-Gated Live Readiness`.
- Issue #165 renamed to `[CRYPTO-P7-01] Policy-gated live promotion preflight`.
- Issues #172 and #173 moved under the P7 milestone.
- Issue comments posted:
  - #151 comment `4654408918`: remote Postgres cutover.
  - #152 comment `4654408995`: runtime DB source update and URL parser fix.
  - #168 comment `4654409152`: live reconciliation runtime checkpoint.
  - #172 comment `4654409298`: calibrated replay/backtest status.
  - #173 comment `4654409458`: account-authoritative reconciliation status.
- Remote Postgres migration artifact:
  `crypto_options_app/artifacts/team_coordination/postgres_remote_migration_20260608.md`.
- Live/replay/trading handoff artifact:
  `crypto_options_app/artifacts/team_coordination/live_engine_strategy_handoff_20260608.md`.
