# Janus Master Controller Automation Contract

Status: draft control contract
Cadence target: every 5 minutes after reconciliation; 10 minutes is acceptable during bootstrap hardening
Mode: one stable controller automation, mutable repo and Obsidian instructions
Current automation id: `janus-master-controller`

## Purpose

The controller automation coordinates Janus work without depending on pinned-chat memory. It reads the repo docs, runtime handoffs, GitHub issue state, and Obsidian references, then decides what work should happen next.

The automation itself should remain stable. Behavior changes should come from editing this contract and adjacent queue/spec files.

## Core Principle

Use Codex for reasoning, coding, debugging, review, and orchestration. Do not use Codex chat memory as the system memory.

## Activation Gate

The recurring controller must remain paused until the operator manually reconciles today's missing event data in the Codex app and the runtime handoffs are refreshed.

The controller may still be used manually for docs/source-of-truth cleanup while paused, but it should not run as recurring automation until the reconciliation gate is cleared.

## Control Actors

| Actor | Role |
|---|---|
| User | Final operator, manual intervention authority, strategic direction. |
| Codex | Reasoning/coding/orchestration agent, fallback strategy crafter, live analyst when invoked. |
| Janus | App-owned runtime, data ingestion, CLOB reconciliation, strategy evaluation, execution gates, ledger. |

All actor actions must be attributable in event inventory and postgame review.

## Required Read Order

Every controller pass must read or inspect:

1. `app/docs/planning/current/final_system/README.md`
2. `app/docs/planning/current/final_system/source_of_truth_map.md`
3. `app/docs/planning/current/final_system/market_scope_registry.md`
4. `app/docs/planning/current/final_system/premise_decisions_2026-05-17.md`
5. `app/docs/planning/current/final_system/automation/master_controller_contract.md`
6. `app/docs/planning/current/final_system/automation/controller_decision_tree.md`
7. `app/docs/planning/current/final_system/automation/agent_persona_registry.md`
8. `app/docs/planning/current/final_system/automation/task_queue_schema.md`
9. `app/docs/planning/current/final_system/automation/issue_taxonomy.md`
10. `app/docs/planning/current/final_system/automation/backlog_layers.md`
11. `app/docs/planning/current/final_system/automation/subagent_parallelism_contract.md`
12. `app/docs/planning/current/final_system/automation/global_portfolio_explorer_contract.md`
13. `app/docs/planning/current/final_system/automation/docs_memory_health_check.md`
14. `app/docs/planning/current/final_system/backlog/immediate_issue_seed_2026-05-17.md`
15. `app/docs/planning/current/final_system/obsidian/bootstrap_map.md`
16. `local/shared/handoffs/daily-live-validation/status.md`
17. `local/shared/handoffs/development-agent/status.md`
18. Latest relevant daily reports.
19. GitHub issue state once the issue seed is created.
20. Obsidian index notes once populated.
21. `python codex_tool/janus_status.py` unless explicitly in docs-only mode.

## Axis-First Decision Model

The controller must classify work across these axes before choosing a persona:

| Axis | Examples |
|---|---|
| `market_domain` | `sports`, `global-portfolio`, `crypto`, `geopolitics`, `economics` |
| `market_subdomain` | `basketball/nba`, `basketball/wnba`, `btc-up-down` |
| `event_lifecycle` | `pregame`, `live`, `postgame`, `settlement`, `monitor`, `research` |
| `janus_control_level` | `janus-controlled`, `codex-assisted`, `operator-manual`, `watch-only` |
| `system_work_mode` | `monitoring`, `planning`, `review`, `development`, `docs-sync`, `issue-triage`, `no-op` |
| `maturity_stage` | `idea`, `research`, `shadow`, `min-size-test`, `live-limited`, `active`, `scaled` |
| `risk_state` | `protect-only`, `base-scalp`, `realized-profit-expansion`, `tail-sleeve` |

Basketball-specific lifecycle terms such as `pregame`, `live`, and `postgame` must not be treated as universal modes for every market domain.

## Timeframe Decision Tree

The controller should derive America/Sao_Paulo time and evaluate:

1. Are NBA/WNBA games live or within a live-monitor window?
2. Are there closed events since the last completed postgame review?
3. Are there games starting soon enough to require integrity or pregame planning?
4. Is the app in a no-live window where development can run?
5. Is a development task already in progress or blocked?
6. Are there unresolved safety issues that block live testing?
7. Are repo docs, Obsidian, or GitHub issue state stale enough to need housekeeping?

The detailed routing rules live in `automation/controller_decision_tree.md`.

## Operating Modes

| Mode | Trigger | Allowed Work |
|---|---|---|
| `live_monitor` | Active NBA/WNBA live game with Janus scope | Monitor only, patch critical bugs, reconcile CLOB, submit reviewed strategy changes, no broad development. |
| `pregame_integrity` | Game window approaching and no fresh integrity gate | Run integrity checks, identify blockers, no orders. |
| `pregame_planning` | Integrity allows planning and watched events exist | Research, plan, StrategyPlanJSON proposals, no orders. |
| `postgame_review` | Closed events exist and no review completed | Build event review/report/development handoff. |
| `development` | No live/pregame/postgame urgent work | Work issue-backed task queue on branch/worktree. |
| `system_organization` | Docs/issues/Obsidian/queue incomplete | Maintain source-of-truth system and issue backlog. |
| `global_portfolio_review` | Daily or ad hoc global portfolio watch-only review | Use `global_portfolio_explorer_contract.md`; read-only account/portfolio analysis and Obsidian/GitHub follow-up only. |
| `no_op` | Nothing safe or useful to do | Write short status only if useful. |

## Live-Game Rule

During active games, no backlog development should run. Only these are allowed:

- Live safety inspection.
- Direct CLOB reconciliation.
- Critical runtime patching.
- StrategyPlanJSON revision or Codex fallback strategy work.
- Event inventory adoption.
- Issue creation for non-urgent defects.

## Internal LLM Failure Rule

If internal LLM is unavailable or cost-blocked:

1. Janus should expose the state in API/DB/runtime artifacts.
2. Deterministic/ML lanes continue if approved and profitable.
3. Codex may be invoked as fallback strategy crafter.
4. Codex output must be converted into StrategyPlanJSON or reviewed action artifacts.
5. Janus validators and order-manager paths still apply unless the user performs manual external intervention.

## Development Rule

Development tasks must be issue-backed after the issue seed is initialized.

Each task should state:

- Issue id or issue draft id.
- Branch/worktree ownership.
- Files owned.
- Tests expected.
- Runtime impact.
- Live-order impact.
- Acceptance criteria.

After any commit, the acting persona must pull/rebase or fast-forward if needed and push the branch to GitHub. GitHub is the operator's current remote interaction surface.

## Controller Outputs

Each controller pass should update or append:

- `local/shared/handoffs/daily-live-validation/status.md` for live/readiness state.
- `local/shared/handoffs/development-agent/status.md` for development state when relevant.
- A final-system queue/status artifact once implemented.
- GitHub issues for durable backlog items.
- Obsidian notes only when the pass adds curated knowledge or cross-links.

## No-Change Compression

The controller runs frequently, so it must not create status noise when state is unchanged.

If the previous pass already recorded the same mode, API/service state, queue decision, and live-money gate:

- Do not append another full daily-status block.
- Do not create a new per-pass artifact unless at least 60 minutes passed since the last artifact or an explicit health checkpoint is due.
- Return a quiet heartbeat summary instead.
- Only write files when there is a material change, a missing required artifact, a scheduled health checkpoint, or a transition toward live/pregame/postgame/development mode.

Material changes include:

- Janus API/service state changes.
- Live-money, LLM dispatch, CLOB, or integrity gate changes.
- Queue rank, issue state, ownership, or blocker changes.
- New game-window urgency.
- New or missing required docs, Obsidian notes, reports, or artifacts.
- Any code/docs/test implementation work.

## Automation Prompt Contract

The actual recurring automation prompt should be short:

```text
Run one Janus master controller pass from the repo root. Read app/docs/planning/current/final_system/source_of_truth_map.md and app/docs/planning/current/final_system/automation/master_controller_contract.md, then follow the referenced mutable source-of-truth docs. Do not rely on chat memory when repo/runtime/GitHub/Obsidian state is available. Stop after one bounded pass and update the appropriate artifacts, reports, handoffs, issues, or notes only when state materially changes.
```

All detailed behavior belongs in repo docs, not in the immutable automation prompt.
