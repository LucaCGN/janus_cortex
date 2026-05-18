# Janus Source Of Truth Map

Status: draft control contract
Created: 2026-05-17

## Purpose

Define where the master controller, Codex agents, Janus services, GitHub issues, runtime artifacts, and Obsidian notes get their authority.

The recurring automation prompt should remain small and stable. It should always point to the same anchor files. The behavior behind those anchors evolves by editing repo docs, GitHub issues, runtime handoffs, and Obsidian notes.

## Authority Stack

| Rank | Source | Authority |
|---:|---|---|
| 1 | Direct CLOB truth | Live collateral, open orders, fills, positions, execution reality |
| 2 | Janus DB/API | App state, events, markets, outcome links, plans, recorded decisions |
| 3 | Runtime artifacts under `local/shared/artifacts` | Machine-readable evidence and replay material |
| 4 | Runtime handoffs under `local/shared/handoffs` | Current operating status, locks, blockers, active tasks |
| 5 | Runtime reports under `local/shared/reports` | Durable review/development evidence |
| 6 | Tracked repo docs | Stable contracts, controller rules, schemas, app specs |
| 7 | GitHub issues | Durable backlog identity, priority, ownership, acceptance criteria |
| 8 | Obsidian curated notes | Design rationale, operator memory, strategy wisdom, case/profile knowledge |
| 9 | Chat and inference | Temporary context only |

GitHub issues are below tracked repo docs because they do not define app behavior by themselves. They are the durable work queue and governance layer.

## Stable Automation Anchors

The controller automation should read these anchor files on every material pass:

1. `app/docs/planning/current/final_system/README.md`
2. `app/docs/planning/current/final_system/source_of_truth_map.md`
3. `app/docs/planning/current/final_system/global_ego_and_purpose.md`
4. `app/docs/planning/current/final_system/market_scope_registry.md`
5. `app/docs/planning/current/final_system/automation/master_controller_contract.md`
6. `app/docs/planning/current/final_system/automation/master_automation_system_prompt.md`
7. `app/docs/planning/current/final_system/automation/controller_decision_tree.md`
8. `app/docs/planning/current/final_system/automation/agent_persona_registry.md`
9. `app/docs/planning/current/final_system/automation/task_queue_schema.md`
10. `app/docs/planning/current/final_system/automation/issue_taxonomy.md`
11. `app/docs/planning/current/final_system/automation/backlog_layers.md`
12. `app/docs/planning/current/final_system/automation/subagent_parallelism_contract.md`
13. `app/docs/planning/current/final_system/automation/codex_tooling_contract.md`
14. `app/docs/planning/current/final_system/automation/global_portfolio_manager_contract.md`
15. `app/docs/planning/current/final_system/automation/global_portfolio_manager_prompt.md`
16. `app/docs/planning/current/final_system/automation/global_portfolio_explorer_contract.md`
17. `app/docs/planning/current/final_system/automation/global_portfolio_explorer_prompt.md`
18. `app/docs/planning/current/final_system/architecture/current_architecture_and_degradation_map.md`
19. `app/docs/planning/current/final_system/backlog/premise_to_backlog_map_2026-05-18.md`
20. `app/docs/planning/current/final_system/obsidian/bootstrap_map.md`
21. `app/docs/planning/current/final_system/obsidian/modular_curation_policy.md`

The automation prompt should not encode detailed persona rules, market taxonomy, issue labels, or backlog policy directly.

## Runtime State Anchors

The controller should inspect these runtime surfaces when they exist:

| Surface | Role |
|---|---|
| `python codex_tool/janus_status.py` | API/service status summary |
| `codex_tool/*` | Current compatibility Codex-to-Janus wrapper surface; target split is defined in `automation/codex_tooling_contract.md` and `#53` |
| `local/shared/handoffs/daily-live-validation/status.md` | Global live/readiness handoff |
| `local/shared/handoffs/development-agent/status.md` | Development lane handoff |
| `local/shared/handoffs/development-agent/master_queue.md` | Local operational queue bridge |
| `local/shared/artifacts/final-system-controller/` | Controller pass artifacts and lock snapshots |
| `local/shared/artifacts/final-system-controller/queue/active_locks/*.json` | Active controller/persona issue and resource claims |
| `local/shared/artifacts/final-system-controller/queue/pass_ledger.jsonl` | Controller pass ledger for claims, no-ops, blockers, releases, and material outputs |
| `local/shared/reports/daily-live-validation/` | Dated postgame/development/integrity reports |
| `python tools/run_janus_startup_reconciliation.py --start-date <YYYY-MM-DD> --days <N>` | Reproducible post-startup data-refresh reconciliation for a fresh/local DB; read/write data only, no live-order actions |
| `python tools/controller_queue.py status` | Repo-local controller queue/lock/pass-ledger status |

## Local Runtime Root

Current default runtime state belongs under the repo-local `local` folder.

If `JANUS_LOCAL_ROOT` is set, all helpers should honor it. If it is unset, Python and PowerShell helpers must resolve to:

`C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex\local`

Do not use legacy external local-root paths for current work. Obsidian remains the curated knowledge vault, not a runtime-artifact root.

## Controller Activation Gate

The recurring controller should remain paused until:

1. Today's missing event data is reconciled through an explicit Codex/operator reconciliation pass.
2. Runtime handoffs reflect the reconciled state.
3. GitHub `main` is pushed with the current source-of-truth docs.
4. The controller can read the repo-local runtime root consistently.
5. The controller queue helper is available and can block duplicate/stale/dirty write scopes before implementation.

As of the 2026-05-18 bootstrap pass, Codex performed the initial repo-local reconciliation and handoff refresh. The controller should still be manually enabled by the operator in the Codex app only after reviewing the refreshed handoffs and pushed docs.

Validation checkpoint 2026-05-18T07:45Z: issue [#32](https://github.com/LucaCGN/janus_cortex/issues/32) verified that `tools/janus_local.ps1 status` and `tools/janus_db.ps1 status` resolve to the repo-local `local` root from a clean PowerShell shell, and that current final-system docs plus active Obsidian source-of-truth notes do not point to the legacy external runtime root. This validates the runtime-root portion of controller activation. It does not grant live-money readiness; StrategyPlanJSON, direct CLOB, worker, feed freshness, cost, and integrity gates remain event-specific requirements.

LLM fallback checkpoint 2026-05-18T08:26Z: issue [#41](https://github.com/LucaCGN/janus_cortex/issues/41) verified budget-aware model routing, explicit internal-LLM/Codex-required fallback state, and reviewed Codex fallback StrategyPlanJSON adoption against Janus safety gates. Request-body LLM revision adoption now preserves trace metadata in the adoption record. This closes the P0 LLM fallback activation blocker, but it does not grant live-money readiness; event-specific StrategyPlanJSON, direct CLOB, worker, feed freshness, cost, and integrity gates still apply.

## Obsidian Relationship

Obsidian should hold curated context:

- strategy principles
- game review memory
- profile studies
- operator preferences
- architecture rationale
- issue/backlog navigation
- market-domain wisdom

Obsidian should not hold live order truth, worker status, active lock authority, or service readiness truth.

Obsidian maintenance must follow `app/docs/planning/current/final_system/obsidian/modular_curation_policy.md`. The default action is to update, merge, split, relink, or mark notes superseded before creating new notes. New notes are valid only when they represent durable concepts, cases, profiles, or source summaries that are linked from the relevant index and overview pages.

## Promotion Rule

If an Obsidian insight becomes operational behavior, it must be promoted into a tracked repo doc, GitHub issue, runtime contract, or code path before the controller treats it as binding.

## GitHub Remote Sync Rule

Every committed change should be pushed to GitHub promptly. GitHub is the operator's current remote interaction surface, so local-only commits are incomplete work unless the operator explicitly asks to keep them local.

## Current Follow-Up Issues

| Issue | Priority | Status | Scope |
|---|---|---|---|
| [#30](https://github.com/LucaCGN/janus_cortex/issues/30) | P0 | closed | GitHub taxonomy labels created; closed seed issues `#17-#29` retro-labeled. |
| [#31](https://github.com/LucaCGN/janus_cortex/issues/31) | P0 | closed | Runtime handoff refresh after 2026-05-18 event reconciliation. |
| [#32](https://github.com/LucaCGN/janus_cortex/issues/32) | P0 | closed | Repo-local runtime-root activation gate validation completed; live readiness remains event-gated. |
| [#33](https://github.com/LucaCGN/janus_cortex/issues/33) | P1 | closed | API-up validation of closed seed foundations completed against the running non-live API. |
| [#34](https://github.com/LucaCGN/janus_cortex/issues/34) | P1 | closed | WNBA minimal-readiness dry run completed; WNBA remains passive/shadow only. |
| [#35](https://github.com/LucaCGN/janus_cortex/issues/35) | P1 | closed | Read-only global portfolio explorer automation. |
| [#36](https://github.com/LucaCGN/janus_cortex/issues/36) | P2 | closed | Absorbed ML replay branch deleted after operator approval. |
| [#37](https://github.com/LucaCGN/janus_cortex/issues/37) | P0 | closed | Fresh-DB NBA probe and account mapping gaps repaired and HTTP-path validated. |
| [#38](https://github.com/LucaCGN/janus_cortex/issues/38) | P0 | closed | Encode Janus global ego and purpose contract in repo prompts and Obsidian. |
| [#39](https://github.com/LucaCGN/janus_cortex/issues/39) | P0 | closed | Controller active queue locks and pass ledger implemented in `app/runtime/controller_queue.py` and `tools/controller_queue.py`. |
| [#40](https://github.com/LucaCGN/janus_cortex/issues/40) | P0 | closed | Current architecture and service degradation map completed in `architecture/current_architecture_and_degradation_map.md`. |
| [#41](https://github.com/LucaCGN/janus_cortex/issues/41) | P0 | closed | Budget-aware model routing and Codex fallback StrategyPlanJSON adoption/evaluation path validated. |
| [#42](https://github.com/LucaCGN/janus_cortex/issues/42) | P1 | open | Validate Polymarket minimum order constraints and market-order exception policy. |
| [#43](https://github.com/LucaCGN/janus_cortex/issues/43) | P1 | open | Add chart-equivalent microstructure metrics to event review bundle. |
| [#44](https://github.com/LucaCGN/janus_cortex/issues/44) | P1 | open | Calibrate profit-ratcheted risk ladder from account and DB histories. |
| [#45](https://github.com/LucaCGN/janus_cortex/issues/45) | P1 | open | Build global portfolio target/rebuy ledger and watchlist schema. |
| [#49](https://github.com/LucaCGN/janus_cortex/issues/49) | P1 | closed | Direct open CLOB order mirror endpoint implemented and runtime-validated; portfolio HTTP orders now expose the four current direct open sell targets. |
| [#50](https://github.com/LucaCGN/janus_cortex/issues/50) | P1 | open | Run passive WNBA CLOB capture and season-level shadow backtests. |
| [#52](https://github.com/LucaCGN/janus_cortex/issues/52) | P0 | open | Define active portfolio-manager execution policy and trend lane. |
| [#53](https://github.com/LucaCGN/janus_cortex/issues/53) | P0 | open | Split Codex tooling into Janus wrappers and independent Polymarket execution fallback. |
| [#46](https://github.com/LucaCGN/janus_cortex/issues/46) | P2 | open | Turn winning profile studies into benchmark hypotheses. |
| [#47](https://github.com/LucaCGN/janus_cortex/issues/47) | P2 | open | Incubate crypto up/down options research and backtest lane. |
| [#48](https://github.com/LucaCGN/janus_cortex/issues/48) | P2 | open | Incubate geopolitics, economics, and culture monitoring lanes. |
