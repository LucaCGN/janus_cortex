# GitHub Source Of Truth Sync

Updated: 2026-06-07T06:40:00Z

Repository: `LucaCGN/janus_cortex`

## Status

- Labels created: `crypto-options`, `transition`, `db-data`, `signals`, `strategies`, `frontend`, `promotion-policy`, `safety`, `repo-cleanup`, `blocked`, `ready-for-fixed-chat`.
- Milestones created: `CRYPTO-P4 Transition Control Plane`, `CRYPTO-P5 Signal And Strategy Queue Trust`, `CRYPTO-P6 Frontend Control Center`, `CRYPTO-P7 Supervised Live Readiness`.
- Issues created: `20`.
- Draft PR created: [#170 Crypto options compatibility wrapper cutover](https://github.com/LucaCGN/janus_cortex/pull/170).
- Runtime DB adapter audit: 0 production runtime direct SQLite blockers, 0 review-required SQLite usages, 11 allowed migration/test/compat usages; legacy profile/market SQLite stores are fenced to research compatibility CLIs and tracked under issue [#152](https://github.com/LucaCGN/janus_cortex/issues/152).
- Redis hot-plane gate: adapter tests now cover JSON TTL cache, NX/EX owner locks, owner-checked release, and RESP command framing; Redis remains disabled until a measured use case is selected, tracked under issue [#151](https://github.com/LucaCGN/janus_cortex/issues/151).
- Promotion/demotion policy rendering: strategy promotion summaries expose `crypto_options_promotion_policy_contract_v1`, including non-promotable signal labels, strict replay gate, 12+ distinct recent samples, strictly >70% win rate, positive PnL, lifecycle/reconciliation, drift/demotion blockers, and no chat/automation live authority; tracked under issue [#153](https://github.com/LucaCGN/janus_cortex/issues/153).
- Frontend fixed chat: ready.
- Signal/strategy cleanup fixed chat: ready to start from GitHub issue source-of-truth.

## Milestones

- `CRYPTO-P4 Transition Control Plane`: 5 open issues.
- `CRYPTO-P5 Signal And Strategy Queue Trust`: 5 open issues.
- `CRYPTO-P6 Frontend Control Center`: 5 open issues.
- `CRYPTO-P7 Supervised Live Readiness`: 5 open issues.

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

### CRYPTO-P7 Supervised Live Readiness

- [#165 [CRYPTO-P7-01] Supervised live promotion preflight](https://github.com/LucaCGN/janus_cortex/issues/165)
- [#166 [CRYPTO-P7-02] Live demotion and stop-gate enforcement](https://github.com/LucaCGN/janus_cortex/issues/166)
- [#167 [CRYPTO-P7-03] Budget scaling and descaling policy](https://github.com/LucaCGN/janus_cortex/issues/167)
- [#168 [CRYPTO-P7-04] Live reconciliation and shadow/live drift audit](https://github.com/LucaCGN/janus_cortex/issues/168)
- [#169 [CRYPTO-P7-05] First live candidate readiness report](https://github.com/LucaCGN/janus_cortex/issues/169)

## Fixed Chat Start Points

- Frontend Control Center Developer: `crypto_options_app/artifacts/team_coordination/fixed_chat_prompts/frontend_control_center_developer.md`, primarily issues #160-#164.
- Signal And Strategy Management Cleanup: `crypto_options_app/artifacts/team_coordination/fixed_chat_prompts/signal_strategy_management_cleanup.md`, primarily issues #155-#159.

## Safety

GitHub issues do not authorize live trading. Supervised live remains gated by promotion policy, reconciliation, lifecycle coverage, and explicit runtime authorization.
