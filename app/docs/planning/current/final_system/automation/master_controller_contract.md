# Janus Master Controller Automation Contract

Status: draft control contract
Cadence target: every 15 minutes after the 2026-05-20 oversight split
Mode: one stable development/live-readiness executor automation, with separate portfolio and dev-loop oversight heartbeats
Current automation display name: `janus-master-dev` (legacy automation id may remain `janus-master-controller` in Codex config)

## Purpose

The controller automation coordinates Janus work without depending on pinned-chat memory. It reads the repo docs, runtime handoffs, GitHub issue state, and Obsidian references, then decides what work should happen next.

The automation itself should remain stable. Behavior changes should come from editing this contract and adjacent queue/spec files.

The master controller is no longer the portfolio oversight lane. Portfolio strategy quality, trade-rationale lifecycle drift, and winning-profile/action quality are monitored by `oversight-portfolio`; the active global portfolio actions themselves belong to `janus-portfolio-manager`. Development-loop health, stale repeated comments, issue splitting/closure, dirty-worktree cleanup, and anti-stagnation checks belong to `oversight-devloop`. The master controller should still observe those lanes when they block Janus live readiness, but it should not spend repeated passes acting as their primary reviewer.

The master controller must also distinguish Janus covered-market live runtime work from Codex global portfolio work. Issue [#63](https://github.com/LucaCGN/janus_cortex/issues/63) owns the Janus FastAPI/live-worker/signal-aggregation redesign. Closed issues [#56](https://github.com/LucaCGN/janus_cortex/issues/56) and [#59](https://github.com/LucaCGN/janus_cortex/issues/59) are global portfolio-manager foundations and must not be used to block or absorb NBA/WNBA covered-market runtime implementation; future portfolio drift or expansion needs a focused follow-up issue.

## Core Principle

Use Codex for reasoning, coding, debugging, review, and orchestration. Do not use Codex chat memory as the system memory.

## Activation Gate

The recurring controller must remain paused until today's missing event data is reconciled through an explicit Codex/operator reconciliation pass and the runtime handoffs are refreshed.

The controller may still be used manually for docs/source-of-truth cleanup while paused, but it should not run as recurring automation until the reconciliation gate is cleared.

As of the 2026-05-18 bootstrap pass, Codex performed an initial repo-local reconciliation and handoff refresh. The operator should still review the pushed docs and refreshed handoffs before manually enabling the recurring Codex app automation.

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
3. `app/docs/planning/current/final_system/global_ego_and_purpose.md`
4. `app/docs/planning/current/final_system/market_scope_registry.md`
5. `app/docs/planning/current/final_system/premise_decisions_2026-05-17.md`
6. `app/docs/planning/current/final_system/automation/master_controller_contract.md`
7. `app/docs/planning/current/final_system/automation/master_automation_system_prompt.md`
8. `app/docs/planning/current/final_system/automation/controller_decision_tree.md`
9. `app/docs/planning/current/final_system/automation/agent_persona_registry.md`
10. `app/docs/planning/current/final_system/automation/task_queue_schema.md`
11. `app/docs/planning/current/final_system/automation/issue_task_register.md`
12. `app/docs/planning/current/final_system/automation/issue_taxonomy.md`
13. `app/docs/planning/current/final_system/automation/backlog_layers.md`
14. `app/docs/planning/current/final_system/automation/subagent_parallelism_contract.md`
15. `app/docs/planning/current/final_system/automation/codex_tooling_contract.md`
16. `app/docs/planning/current/final_system/automation/global_portfolio_manager_contract.md`
17. `app/docs/planning/current/final_system/automation/global_portfolio_manager_prompt.md`
18. `app/docs/planning/current/final_system/automation/global_portfolio_explorer_contract.md`
19. `app/docs/planning/current/final_system/automation/global_portfolio_explorer_prompt.md`
20. `app/docs/planning/current/final_system/automation/docs_memory_health_check.md`
21. `app/docs/planning/current/final_system/backlog/immediate_issue_seed_2026-05-17.md`
22. `app/docs/planning/current/final_system/backlog/premise_to_backlog_map_2026-05-18.md`
23. `app/docs/planning/current/final_system/obsidian/bootstrap_map.md`
24. `app/docs/planning/current/final_system/obsidian/modular_curation_policy.md`
25. `local/shared/handoffs/daily-live-validation/status.md`
26. `local/shared/handoffs/development-agent/status.md`
27. Latest relevant daily reports.
28. GitHub issue state once the issue seed is created.
29. Obsidian index notes once populated.
30. `python codex_tool/janus_status.py` unless explicitly in docs-only mode.

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
8. Are there open unblocked sprint implementation issues that should be claimed before no-op?

The detailed routing rules live in `automation/controller_decision_tree.md`.

For NBA/WNBA test days, the controller must distinguish passive shadow capture from Janus covered-market live readiness. If a covered NBA game is inside the pregame/live-monitor window and `current_plan_count_today=0`, the next useful action is a bounded StrategyPlanJSON/pregame-plan submission or a concrete plan-crafting blocker. Repeating WNBA passive captures with `orders_allowed=false` does not clear the NBA StrategyPlan gate.

After an explicit operator-approved minimum-size covered-market order has been submitted through the Janus StrategyPlan execute path, the controller must preserve the current plan for monitoring but disable duplicate entries by revising the sleeve to post-order monitor-only (`shadow_only=true`, `entry_disabled=true`, live external order id recorded). The next pass should verify direct CLOB order/fill state, live game state, target/stop/rebuy policy, and reconciliation evidence before any new entry or worker start.

During an active covered NBA/WNBA live game, the controller is also a game/market analyst for Janus infrastructure. It must keep the newest machine-readable live checkpoint fresh enough for the current phase and summarize score/clock/period, market movement, direct CLOB current-event inventory, pending intents, LLM/runtime triggers, and blockers. No-change compression must not suppress this checkpoint when the prior artifact is stale, lacks direct current-event inventory, or conflicts with fresher evidence.

After a covered NBA/WNBA game reaches final or settlement, unresolved event-scoped direct CLOB orders, positions, fills, or valuation mismatches are live-readiness blockers for the next event. They must route to a settlement/reconciliation issue before any new worker enablement or new live-order test. The 2026-05-18 Spurs/Thunder residual Thunder order/position is owned by [#57](https://github.com/LucaCGN/janus_cortex/issues/57). Closed [#50](https://github.com/LucaCGN/janus_cortex/issues/50) is the WNBA passive/shadow baseline report; closed [#60](https://github.com/LucaCGN/janus_cortex/issues/60) is the completed WNBA active-window passive capture/audit foundation.

Resolved-market `Redeem` is a settlement capability, not a normal close/sell order. The controller must not execute redemption itself. It may route implementation to [#58](https://github.com/LucaCGN/janus_cortex/issues/58), record a documented residual classification, or require a redeem preview. A documented residual requires fresh direct account/CLOB truth, resolved market/token/outcome state, expected payout/current value, no event-scoped open orders, ledger or issue linkage, and a post-redeem/recheck plan. Once those gates prove a zero-value losing residual or redeemable settlement row is not active exposure, Janus should keep operating with the unredeemed row rather than blocking unrelated new-game readiness. Non-dry-run redemption requires explicit Janus+Codex operator approval gates and must never be inferred from screenshots, chat memory, Obsidian, or stale mirrors.

After [#57](https://github.com/LucaCGN/janus_cortex/issues/57), [#58](https://github.com/LucaCGN/janus_cortex/issues/58), [#60](https://github.com/LucaCGN/janus_cortex/issues/60), and [#61](https://github.com/LucaCGN/janus_cortex/issues/61) are closed, the controller must not continue treating those issues as the active development route or wait for stale settlement/WNBA/NBA foundation evidence before doing useful work. The active sports readiness route is [#62](https://github.com/LucaCGN/janus_cortex/issues/62) for WNBA promotion from shadow capture to controlled minimum-size live readiness; future NBA runtime gaps should be split into focused [#63](https://github.com/LucaCGN/janus_cortex/issues/63), [#70](https://github.com/LucaCGN/janus_cortex/issues/70), or [#55](https://github.com/LucaCGN/janus_cortex/issues/55) follow-ups instead of reopening #61. In a no-live/no-pregame window with #62 freshly checkpointed or explicitly blocked, the expected route is an open sprint issue with a bounded slice such as #55 entry-timing/template guidance, a #63 child issue, #70 replay/config calibration, or a new focused global-portfolio follow-up if the portfolio manager exposes fresh drift after closed foundations #56/#59. If it cannot claim or work the selected issue, it must record the concrete blocker once.

## Operating Modes

| Mode | Trigger | Allowed Work |
|---|---|---|
| `live_monitor` | Active NBA/WNBA live game with Janus scope | Monitor only, patch critical bugs, reconcile CLOB, submit reviewed strategy changes, no broad development. |
| `pregame_integrity` | Game window approaching and no fresh integrity gate | Run integrity checks, identify blockers, no orders. |
| `pregame_planning` | Integrity allows planning and watched events exist | Research, plan, StrategyPlanJSON proposals, no orders. |
| `postgame_review` | Closed events exist and no review completed | Build event review/report/development handoff. |
| `development` | No live/pregame/postgame urgent work | Work issue-backed task queue on branch/worktree. |
| `system_organization` | Docs/issues/Obsidian/queue incomplete | Maintain source-of-truth system and issue backlog. |
| `global_portfolio_management` | Daily or ad hoc Codex global portfolio management/scouting pass | Use `global_portfolio_manager_contract.md` and `codex_tooling_contract.md`; manage existing operator/global positions, scout uncovered trend opportunities, and execute only through approved portfolio order-management or independent Polymarket fallback gates. This is not the internal Janus covered-market portfolio/inventory agent for NBA/WNBA. |
| `no_op` | Nothing safe or useful to do | Write short status only if useful. |

`no_op` is not valid while an open unblocked P0/P1 implementation issue has a bounded next slice and no higher-priority live/pregame/postgame route is active.

In the five-lane automation topology, `global_portfolio_management` is normally executed by `janus-portfolio-manager`, not by `janus-master-dev`. The master controller may route or patch bounded portfolio tooling/docs only under an active focused portfolio follow-up when no covered NBA/WNBA readiness work preempts it; closed #56/#59 foundations should not be reopened as broad status buckets.

## Live-Game Rule

During active games, no backlog development should run. Only these are allowed:

- Live safety inspection.
- Game/market analyst summaries that use Janus runtime, scoreboard, orderbook, and direct CLOB evidence.
- Direct CLOB reconciliation.
- Critical runtime patching.
- StrategyPlanJSON revision or Codex fallback strategy work.
- Event inventory adoption.
- Issue creation for non-urgent defects.

The live-monitor pass must prefer fresh runtime artifacts over automation memory. If the newest `live-monitor_*.json` lacks current-event inventory or shows only `live_strategy_worker_not_ready`, the controller should run or request a bounded dry live-strategy checkpoint so direct open orders, fills, and positions are visible before it reports inventory state.

## Internal LLM Failure Rule

If internal LLM is unavailable or cost-blocked:

1. Janus should expose the state in API/DB/runtime artifacts.
2. Deterministic/ML lanes continue if approved and profitable.
3. Codex may be invoked as fallback strategy crafter.
4. Codex output must be converted into StrategyPlanJSON or reviewed action artifacts.
5. Janus validators and order-manager paths still apply unless the user performs manual external intervention.

## Pregame Research Dependency Rule

Pregame Codex/NBA/WNBA research automations should produce useful priors, player/team context, and candidate signal configs. They are not a required live-trading dependency.

If pregame research is missing, stale, paused, or crashed, Janus must enter degraded mode rather than global live-disable:

1. Use stored priors and default event config.
2. Continue deterministic/ML signal producers whose feed, CLOB, risk, worker, kill-switch, and order-path gates are green.
3. Disable only the missing research/LLM signal source and record the degraded input in the runtime artifact.
4. Fail closed only when a required runtime gate is red, such as stale feed, missing direct CLOB, no current event mapping, no worker heartbeat, kill-switch active, risk cap exhausted, or order-path preflight failure.

## Development Rule

Development tasks must be issue-backed after the issue seed is initialized.

Each task should state:

- Issue id or issue draft id.
- Issue task register id from `automation/issue_task_register.md` when the issue is already open and selected for work.
- Branch/worktree ownership.
- Files owned.
- Tests expected.
- Runtime impact.
- Live-order impact.
- Acceptance criteria.

Before selecting an open P0/P1 issue for implementation, check `automation/issue_task_register.md`. The pass should work the listed next executable step, update the register when the task decomposition or blocker changed, or add a missing task before commenting on the issue. A repeated GitHub issue comment with no register change, validation evidence, commit, closure, or blocker change is `no_material_change`.

Before any code, docs, handoff, Obsidian, or runtime-artifact write, the acting persona must establish ownership through the repo-local controller queue:

```powershell
python tools/controller_queue.py claim --issue <issue_number> --persona <persona> --owner janus-master-controller --branch <branch> --worktree C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex --file <path> --module <module> --require-clean-worktree
```

The claim must include the issue and every expected file/module/event/service/runtime scope. If the claim is blocked by an active duplicate, stale lock, or dirty worktree, the pass must stop writing and record the blocker once in the pass ledger. Stale locks are surfaced for review; they are not silently overwritten.

After completing or abandoning the slice, the acting persona must release the lock with the outcome and evidence:

```powershell
python tools/controller_queue.py release --lock-id <lock_id> --outcome implemented_partial --material-output <commit-or-artifact> --evidence <issue-link>
```

After any commit, the acting persona must pull/rebase or fast-forward if needed and push the branch to GitHub. GitHub is the operator's current remote interaction surface.

## Dirty Worktree Completion Gate

The shared repo must not accumulate uncommitted issue-backed work across released locks.

At the beginning and end of every controller pass, inspect tracked git status and queue locks:

- If tracked files are dirty before a new claim and no active lock owns those paths, do not start unrelated implementation. Route to `development-end-phase` or `master-controller` cleanup.
- If dirty paths span multiple issues or personas, classify the pass as `YELLOW` process drift, map paths to issue scope, and run validation for the smallest coherent slice.
- If dirty paths are fully covered by one fresh active lock, do not treat the dirty worktree as a passive global blocker. Route to the owning issue: advance validation, commit/push, split the lock, release with an exact blocker, or explicitly defer only when a higher-priority live safety route preempts it.
- If unrelated work is blocked only by a fresh lock on disjoint files, either use a separate clean worktree/issue slice or escalate to `master-janus-manager` / `oversight-devloop` to finish or split the owning lock. Repeating the same "dirty worktree blocks implementation" sentence without moving the owner issue is `YELLOW` automation stagnation.
- If validation passes and ownership is clear, commit and push the coherent slice. If ownership is unclear or the slice includes operator/user edits that cannot be safely attributed, stop and request operator review.
- Do not keep adding GitHub comments, handoff blocks, or runtime artifacts for unrelated issues while dirty mixed-scope work is unresolved.
- Live safety still outranks cleanup, but only for the narrow live intervention. After the live fix, cleanup becomes the next required action.

A development slice is not complete merely because tests passed. It is complete only when pushed, or when any remaining dirty files are explicitly owned by an active lock with a documented next validation/commit command.

## Issue Progress Discipline

The controller must not confuse issue commentary with issue progress.

When a pass selects an open issue as the next safe task:

- If the issue is executable now and no live/pregame/postgame safety gate blocks it, the pass should claim one bounded slice and attempt implementation, validation, commit, push, and issue update.
- A claim means a successful `tools/controller_queue.py claim` entry exists for the issue and write scope before edits begin.
- If the selected issue is too large for one pass, the pass must reduce it to the smallest useful slice with file/module ownership, tests, and expected evidence, then start that slice or hand it to the development-agent status.
- If the issue is blocked, the pass must record the exact blocker and the next unblock action. Repeating the same blocker is a no-op unless new evidence changes the blocker, priority, owner, or acceptance criteria.
- A GitHub issue comment is progress only when it changes durable issue state: acceptance criteria, blocker state, owner/lock, reproduction, validation result, commit link, or closure rationale.
- A solved issue must have a commit pushed to GitHub, validation evidence, and an issue update or closure. Runtime artifacts alone are not a completed development outcome.
- The controller should treat repeated comments on the same open issue without a fix, claim, blocker change, or handoff as `YELLOW` process drift.
- Close-or-split rule: if the original acceptance criteria are satisfied and only broader calibration, promotion, or execution hardening remains, close the solved issue and create or update a smaller follow-up issue for the remaining work. Do not keep an umbrella issue open only to collect repeated status comments.
- Parallelism rule: when several unblocked issues have disjoint files/modules/events/services/markets and no live-game safety preemption, the controller should route them as parallel-safe bounded slices instead of serializing unrelated work behind a solved or blocked issue.
- Today's NBA/WNBA readiness tasks outrank global-portfolio expansion unless direct live-money safety is unclear. The Codex global portfolio manager must not monopolize the development lane while sports test blockers remain open.

For P0 issues, a normal development loop should complete at least one issue-sized unit or one explicitly defined sub-slice. If it cannot, it must leave a concrete next command, file scope, and validation plan instead of another general status comment.

## Controller Outputs

Each controller pass should update or append:

- `local/shared/handoffs/daily-live-validation/status.md` for live/readiness state.
- `local/shared/handoffs/development-agent/status.md` for development state when relevant.
- A final-system queue/status artifact once implemented.
- GitHub issues for durable backlog items.
- Obsidian notes only when the pass adds curated knowledge or improves existing navigation, following `obsidian/modular_curation_policy.md`.

## No-Change Compression

The controller runs frequently, so it must not create status noise when state is unchanged.

If the previous pass already recorded the same mode, API/service state, queue decision, and live-money gate:

- Do not append another full daily-status block.
- Do not create a new per-pass artifact unless at least 60 minutes passed since the last artifact or an explicit health checkpoint is due.
- Return a quiet heartbeat summary instead.
- Optionally append a compact pass-ledger entry with `tools/controller_queue.py ledger` when the no-op state matters for later review.
- Only write files when there is a material change, a missing required artifact, a scheduled health checkpoint, or a transition toward live/pregame/postgame/development mode.

During active covered live games, scoreboard phase changes, meaningful orderbook movement, direct CLOB order/fill/position changes, LLM/runtime triggers, and stale or incomplete live artifacts are material. A quiet no-op is valid only after the latest live checkpoint is fresh and includes current-event inventory.

Dirty tracked files without an active owning lock are also material. No-change compression cannot hide a dirty mixed-scope worktree.

Material changes include:

- Janus API/service state changes.
- Live-money, LLM dispatch, CLOB, or integrity gate changes.
- Queue rank, issue state, ownership, or blocker changes.
- New game-window urgency.
- New or missing required docs, Obsidian notes, reports, or artifacts.
- Any code/docs/test implementation work.

## Automation Prompt Contract

The full structured prompt lives in:

`app/docs/planning/current/final_system/automation/master_automation_system_prompt.md`

The actual recurring Codex app automation prompt should stay short and point to that file:

```text
Run one Janus Master Controller pass from C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex. Treat app/docs/planning/current/final_system/automation/master_automation_system_prompt.md as the controlling system instruction and follow the mutable source-of-truth docs it references. Do not rely on chat memory when repo/runtime/GitHub/Obsidian state is available. Do not place, cancel, replace, or submit orders. Stop after one bounded pass and write artifacts, handoffs, issues, repo docs, or Obsidian notes only when state materially changes.
```

All detailed behavior belongs in repo docs, not in the immutable automation prompt.
