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
3. `app/docs/planning/current/final_system/market_scope_registry.md`
4. `app/docs/planning/current/final_system/automation/master_controller_contract.md`
5. `app/docs/planning/current/final_system/automation/controller_decision_tree.md`
6. `app/docs/planning/current/final_system/automation/agent_persona_registry.md`
7. `app/docs/planning/current/final_system/automation/task_queue_schema.md`
8. `app/docs/planning/current/final_system/automation/issue_taxonomy.md`
9. `app/docs/planning/current/final_system/automation/backlog_layers.md`
10. `app/docs/planning/current/final_system/automation/subagent_parallelism_contract.md`
11. `app/docs/planning/current/final_system/obsidian/bootstrap_map.md`

The automation prompt should not encode detailed persona rules, market taxonomy, issue labels, or backlog policy directly.

## Runtime State Anchors

The controller should inspect these runtime surfaces when they exist:

| Surface | Role |
|---|---|
| `python codex_tool/janus_status.py` | API/service status summary |
| `local/shared/handoffs/daily-live-validation/status.md` | Global live/readiness handoff |
| `local/shared/handoffs/development-agent/status.md` | Development lane handoff |
| `local/shared/handoffs/development-agent/master_queue.md` | Local operational queue bridge |
| `local/shared/artifacts/final-system-controller/` | Controller pass artifacts and lock snapshots |
| `local/shared/reports/daily-live-validation/` | Dated postgame/development/integrity reports |

## Local Runtime Root

Current default runtime state belongs under the repo-local `local` folder.

If `JANUS_LOCAL_ROOT` is set, all helpers should honor it. If it is unset, Python and PowerShell helpers must resolve to:

`C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex\local`

Do not use legacy external local-root paths for current work. Obsidian remains the curated knowledge vault, not a runtime-artifact root.

## Controller Activation Gate

The recurring controller should remain paused until:

1. Today's missing event data is manually reconciled by the operator in the Codex app.
2. Runtime handoffs reflect the reconciled state.
3. GitHub `main` is pushed with the current source-of-truth docs.
4. The controller can read the repo-local runtime root consistently.

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

## Promotion Rule

If an Obsidian insight becomes operational behavior, it must be promoted into a tracked repo doc, GitHub issue, runtime contract, or code path before the controller treats it as binding.

## GitHub Remote Sync Rule

Every committed change should be pushed to GitHub promptly. GitHub is the operator's current remote interaction surface, so local-only commits are incomplete work unless the operator explicitly asks to keep them local.
