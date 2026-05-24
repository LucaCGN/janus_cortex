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
14. `app/docs/planning/current/final_system/automation/live_activation_preflight_contract.md`
15. `app/docs/planning/current/final_system/automation/global_portfolio_manager_contract.md`
16. `app/docs/planning/current/final_system/automation/global_portfolio_manager_prompt.md`
17. `app/docs/planning/current/final_system/automation/global_portfolio_explorer_contract.md`
18. `app/docs/planning/current/final_system/automation/global_portfolio_explorer_prompt.md`
19. `app/docs/planning/current/final_system/automation/live_signal_aggregation_contract.md`
20. `app/docs/planning/current/final_system/architecture/current_architecture_and_degradation_map.md`
21. `app/docs/planning/current/final_system/architecture/janus_core_live_trading_runtime.md`
22. `app/docs/planning/current/final_system/backlog/premise_to_backlog_map_2026-05-18.md`
23. `app/docs/planning/current/final_system/backlog/live_runtime_scope_map_2026-05-24.md`
24. `app/docs/planning/current/final_system/obsidian/bootstrap_map.md`
25. `app/docs/planning/current/final_system/obsidian/modular_curation_policy.md`

The automation prompt should not encode detailed persona rules, market taxonomy, issue labels, or backlog policy directly.

## Current Automation Topology

As of 2026-05-20, the Codex app automation layer is split into five lanes so portfolio trading oversight does not compete with the development/issue loop:

| Automation | Cadence | Scope | Hard boundary |
|---|---:|---|---|
| `oversight-portfolio` | 1 hour | Review `janus-portfolio-manager` behavior, strategy quality, trade-rationale lifecycle, winning-profile delta use, target/close/grid decisions, and #56/#59 evidence. | Oversight only: no order, cancel, replace, redeem, signing, broadcasting, or worker/service start. |
| `oversight-devloop` | 30 minutes | Monitor the development loop, queue locks, dirty worktree, repeated issue comments, issue splitting/closure, backlog drift, and whether implementation slices are being claimed, validated, committed, pushed, and closed. | No trading actions; code/docs patches only when issue-backed and narrow. |
| `janus-master-dev` | 15 minutes | Recurring Janus development/live-readiness executor for issue-backed work, especially #61/#62 sports readiness, #55 research support, #59 activation proof, and bounded #56 tooling/docs work when sports does not preempt it. | Not the global portfolio trader; no raw exchange bypass; Janus live actions only through approved Janus gates. |
| `janus-portfolio-manager` | 30 minutes | Active Codex global portfolio manager for existing-position actions, new micro-position scouting, Polymarket frontend/profile discovery, one-shot order routing, and grid/scalping candidates. | May trade only through approved portfolio-manager order paths and live gates; no Janus NBA/WNBA covered-market authority. |
| `janus-obsidian-builder` | 6 hours | Curated Obsidian memory, indexes, profile/trade rationale navigation, source links, and note hygiene. | No execution, no automation schedule edits, no repo contract rewrites unless explicitly issue-backed. |

Historical automation ids in `C:\Users\lnoni\.codex\automations` may remain for continuity, but the displayed names/prompts should match this topology. If a crash or time-machine restore reverts prompts, restore these five lanes before running portfolio or development loops again.

Observed config drift 2026-05-24: the repo docs define `oversight-portfolio`, but the local automation directory inspection showed `janus-loop-oversight-console`, `janus-master-controller`, `janus-master-controller-2` without `automation.toml`, `janus-obsidian-builder`, `janus-portfolio-manager`, and `oversight-devloop`. Before relying on portfolio oversight, verify that the actual Codex automation config exists and matches the portfolio-only prompt. This drift does not change Janus runtime authority; it is an automation setup task.

Planned current-scope expansion before crypto/options:

| Planned automation | Issue | Scope | Authority |
|---|---|---|---|
| `janus-performance-review` | [#71](https://github.com/LucaCGN/janus_cortex/issues/71) | Daily/project-chief review of live results, missed signals, strategy responsiveness, pregame accuracy, issue progress, and next development priorities. | Read-only trading; may create/update issues, backlog, docs, and Obsidian summaries. |
| `nba-pregame-research` | [#72](https://github.com/LucaCGN/janus_cortex/issues/72) | NBA pregame research as structured optional priors. | Research only; never a liveness dependency. |
| `wnba-pregame-research` | [#72](https://github.com/LucaCGN/janus_cortex/issues/72) | WNBA pregame research as structured optional priors. | Research only; never a liveness dependency. |
| issue lifecycle health pass | [#73](https://github.com/LucaCGN/janus_cortex/issues/73) | Anti-stagnation scoring for repeated comments, stale blockers, unclosed work, and oversized issues. | Governance only; no trading. |
| Obsidian backlog repair | [#74](https://github.com/LucaCGN/janus_cortex/issues/74) | Repair note-to-backlog promotion so curated notes become bounded issue candidates without becoming runtime truth. | Curation/governance only; no trading. |

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
- portfolio trade rationale and post-close lessons
- profile studies
- operator preferences
- architecture rationale
- issue/backlog navigation
- market-domain wisdom

Obsidian should not hold live order truth, worker status, active lock authority, or service readiness truth.

Obsidian maintenance must follow `app/docs/planning/current/final_system/obsidian/modular_curation_policy.md`. The default action is to update, merge, split, relink, or mark notes superseded before creating new notes. New notes are valid only when they represent durable concepts, cases, profiles, or source summaries that are linked from the relevant index and overview pages.

### Portfolio Trade Rationale Registry

The global portfolio manager must use this Obsidian registry as the curated index for Codex/Janus-assisted portfolio trades:

`C:\Users\lnoni\OneDrive\Documentos\Janus\Janus-Brain\20_Trading_Knowledge\Trade Rationale Registry.md`

For every successful non-dry-run portfolio-manager order placement, create or update one linked Obsidian trade rationale note under:

`C:\Users\lnoni\OneDrive\Documentos\Janus\Janus-Brain\20_Trading_Knowledge\Trade Rationales\`

This includes entry/add orders and resting target/exit/rebuy orders even when they do not immediately fill. The registry row must include the trade note path, market/outcome, order timestamp, action type, size/price/notional, external order id when available, action artifact path, direct post-order reconciliation artifact path, status, and related GitHub issue. The individual trade note must hold the thesis, profile/frontend/direct-truth evidence, target/stop/rebuy policy, falsification trigger, risk cap, and artifact links.

For every successful non-dry-run portfolio-manager close/sell/redeem that reduces or exits a recorded position, update the original trade rationale note before treating the lifecycle as complete. The close review must record realized/unrealized result, what worked, caveats, what not to do next time, whether the setup is repeatable, and whether to create/update a GitHub replay/backtest/domain-lane issue.

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
| [#43](https://github.com/LucaCGN/janus_cortex/issues/43) | P1 | closed | Chart-equivalent microstructure metrics implemented for event review/live monitor; later authority promotion belongs to replay/fillability follow-up work. |
| [#44](https://github.com/LucaCGN/janus_cortex/issues/44) | P1 | open | Calibrate profit-ratcheted risk ladder from account and DB histories. |
| [#45](https://github.com/LucaCGN/janus_cortex/issues/45) | P1 | closed | Global portfolio watchlist schema, read-only tooling, target policy flags, and artifacts implemented; active execution moved through `#54` and remaining activation proof is `#59`. |
| [#49](https://github.com/LucaCGN/janus_cortex/issues/49) | P1 | closed | Direct open CLOB order mirror endpoint implemented and runtime-validated; portfolio HTTP orders now expose the four current direct open sell targets. |
| [#50](https://github.com/LucaCGN/janus_cortex/issues/50) | P1 | closed | WNBA passive/shadow baseline and blocker report published; sustained active-window capture/audit moved to closed `#60`. |
| [#52](https://github.com/LucaCGN/janus_cortex/issues/52) | P0 | closed | Active Codex global portfolio-manager policy, prompt, action ledger, and dry-run order-management preview implemented. |
| [#53](https://github.com/LucaCGN/janus_cortex/issues/53) | P0 | closed | Base `codex_tools/janus` and `codex_tools/polymarket` split, preview-first fallback gates, account reads, ledgers, CLI, and compatibility wrappers implemented. |
| [#54](https://github.com/LucaCGN/janus_cortex/issues/54) | P0 | closed | Approved global portfolio execution gate proof, concrete Janus order-management adapter, runtime activation guard, risk/rate evidence, ledger finalization, confirmation-id handling, and idempotency replay hardening implemented. |
| [#56](https://github.com/LucaCGN/janus_cortex/issues/56) | P1 | open | Active portfolio-manager action planning, frontend/profile discovery enforcement, one-shot portfolio order routing, global 1c grid-service spawn proof, and cross-league basketball scanner. |
| [#59](https://github.com/LucaCGN/janus_cortex/issues/59) | P0 | open | Prove portfolio-manager real-call reconciliation before operational activation. |
| [#55](https://github.com/LucaCGN/janus_cortex/issues/55) | P1 | open | NBA entry-timing research for pregame, immediate-live, post-Q1, and event-start cancellation effects; supports #61 but does not own live execution. |
| [#60](https://github.com/LucaCGN/janus_cortex/issues/60) | P1 | closed | WNBA sustained active-window passive CLOB/trade capture and audit integration completed; future WNBA live-promotion is #62. |
| [#61](https://github.com/LucaCGN/janus_cortex/issues/61) | P0 | open | Next NBA playoff min-size live trade route through Janus StrategyPlan/evaluate/execute/live-worker gates. |
| [#62](https://github.com/LucaCGN/janus_cortex/issues/62) | P0 | open | WNBA shadow-to-controlled-min-size live test readiness route. |
| [#63](https://github.com/LucaCGN/janus_cortex/issues/63) | P0 | open | Parent architecture and implementation route for independent Janus covered-market live trading runtime, live signal aggregation, degraded-mode operation, event risk budgets, and issue/backlog reset. |
| [#64](https://github.com/LucaCGN/janus_cortex/issues/64)-[#70](https://github.com/LucaCGN/janus_cortex/issues/70) | P0/P1 | open | Child implementation slices for normalized live snapshots, signal schema, aggregation arbitration, event budgets/sleeves, deterministic fallback, runtime controls, and postgame signal-performance review. |
| [#71](https://github.com/LucaCGN/janus_cortex/issues/71) | P1 | open | Project-chief performance review and development-planning automation that turns live/postgame results into next-day issues, config changes, and closures. |
| [#72](https://github.com/LucaCGN/janus_cortex/issues/72) | P1 | open | Formalize NBA/WNBA pregame research agents as optional priors with expiry and no liveness dependency. |
| [#73](https://github.com/LucaCGN/janus_cortex/issues/73) | P1 | open | Harden issue lifecycle anti-stagnation and closure governance for repeated comments, stale blockers, and oversized issues. |
| [#74](https://github.com/LucaCGN/janus_cortex/issues/74) | P2 | open | Repair Obsidian-to-backlog ingestion and curation workflow so note insights become issue candidates with acceptance criteria. |
| [#46](https://github.com/LucaCGN/janus_cortex/issues/46) | P2 | open | Turn winning profile studies into benchmark hypotheses. |
| [#47](https://github.com/LucaCGN/janus_cortex/issues/47) | P2 | open | Incubate crypto up/down options research and backtest lane. |
| [#48](https://github.com/LucaCGN/janus_cortex/issues/48) | P2 | open | Incubate geopolitics, economics, and culture monitoring lanes. |
