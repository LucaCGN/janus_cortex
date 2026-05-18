# Janus Controller Decision Tree

Status: draft control contract
Created: 2026-05-17

## Purpose

Define how the recurring master controller chooses the next action from market axes, time windows, runtime state, GitHub issues, and active locks.

This document refines the older basketball-only `live / pregame / postgame / development` flow.

## Pass Order

Each controller pass should:

1. Read stable anchor docs from `source_of_truth_map.md`.
2. Read runtime state and handoffs if not in docs-only mode.
3. Read GitHub issue state for open/claimed/blocked tasks.
4. Read Obsidian indexes for curated context, not runtime truth.
5. Build an axis snapshot.
6. Check active locks and running-agent registry.
7. Choose one primary mode and persona.
8. Claim the selected issue/resource scope before any write, or record a blocked/no-op ledger entry.
9. Decide whether sub-agents are allowed.
10. Execute one bounded pass.
11. Release active claims and write outputs in authority-safe order.

## Axis Snapshot

The controller should record:

| Field | Examples |
|---|---|
| `market_domain` | `sports`, `global-portfolio`, `crypto` |
| `market_subdomain` | `basketball/nba`, `basketball/wnba`, `btc-up-down` |
| `event_lifecycle` | `pregame`, `live`, `postgame`, `monitor`, `research` |
| `janus_control_level` | `janus-controlled`, `codex-assisted`, `watch-only` |
| `system_work_mode` | `monitoring`, `planning`, `review`, `development`, `docs-sync`, `no-op` |
| `maturity_stage` | `idea`, `research`, `shadow`, `min-size-test`, `live-limited` |
| `risk_state` | `protect-only`, `base-scalp`, `realized-profit-expansion`, `tail-sleeve` |

## Priority Rules

| Rank | Condition | Persona |
|---:|---|---|
| 1 | Direct CLOB/current-event inventory unsafe, cost runaway, stale live worker, or unclear live-money state | `live-monitor-analyst` or `pregame-integrity` |
| 2 | Active Janus-controlled live event | `live-monitor-analyst` |
| 3 | Closed event lacks postgame review or reconciliation | `postgame-reviewer` |
| 4 | Upcoming event lacks integrity gate | `pregame-integrity` |
| 5 | Upcoming event passed integrity but lacks plan/watchpoints | `pregame-planner` |
| 6 | Development task is claimed or review-ready | `development-agent` or `development-end-phase` |
| 7 | Backlog/issue taxonomy/queue missing or stale | `issue-backlog-manager` |
| 8 | Source-of-truth docs or Obsidian indexes stale | `docs-memory-agent` |
| 9 | Global portfolio management/scouting pass is due and no higher-priority live safety task is active | `global-portfolio-agent` |
| 10 | New market/domain idea needs classification | `future-domain-research-agent` or `profile-research-agent` |
| 11 | No material state change | `master-controller` no-op |

Safety and live event state override backlog progress.

## Recurring Automation Gate

If today's event data has not been reconciled through an explicit Codex/operator reconciliation pass, the controller may run only in docs/source-of-truth cleanup mode.

It must not enable recurring automation, mark live readiness green, or treat runtime handoffs as current until reconciliation is complete.

The 2026-05-18 bootstrap pass performed the first repo-local reconciliation and refreshed handoffs, but the operator still owns manual enablement of the recurring Codex app automation.

## Basketball Lifecycle Rules

| Lifecycle | Rule |
|---|---|
| `live` | No broad development. Monitor, reconcile, issue-create, or patch only critical failures. |
| `pregame` | Integrity precedes planning. Planning does not place orders. |
| `postgame` | Review/reconciliation precedes development planning unless a P0 safety bug blocks all work. |
| `settlement` | Direct CLOB/account truth must be reconciled before performance claims. |

## Global Portfolio Rules

The `janus-portfolio-manager` lane is active-management intent, not a Janus NBA/WNBA trade validator and not merely a read-only explorer:

1. Manage existing operator/global positions: verify direct CLOB truth, matching targets, stale/missing targets, exits, rebuy watches, and concentration risk.
2. Scout uncovered categories for trend-following opportunities where the thesis is trend, market structure, liquidity, and return path rather than direct final-outcome prediction.
3. Execute only through an approved Janus portfolio order-management path after all gates in `automation/global_portfolio_manager_contract.md` are true.
4. If execution gates are missing, update watchlists, Obsidian lessons, GitHub blockers, and runtime evidence without preparing or submitting orders.
5. Successful new-market trades must become backlog tests or Obsidian do/don't lessons before any domain is promoted.

Detailed global-portfolio automation rules live in `automation/global_portfolio_manager_contract.md`. The older explorer contract is retained as read-only discovery context.

## New Domain Rules

New domains follow:

1. `idea`
2. `research`
3. `shadow`
4. `min-size-test`
5. `live-limited`
6. `active`
7. `scaled`

No domain can skip directly from `idea` or `research` to live trading.

## Development Rules

Development may proceed only when:

- no live event requires attention
- no postgame/reconciliation blocker is pending
- task is issue-backed or explicitly docs/bootstrap scoped
- write locks are clear
- tests/validation expectations are known
- live-order impact is explicit

Write locks are clear only when `python tools/controller_queue.py claim` succeeds for the selected issue and write scope. Duplicate active locks block the pass. Stale active locks are review blockers, not permission to overwrite. A dirty shared worktree before the claim blocks implementation unless the dirty files are already owned by the active claim.

## Issue Progress Rules

The controller should prefer finishing useful issue-sized work over producing repeated status commentary.

When the same open issue remains the selected route across consecutive passes:

- Do not add another GitHub comment or full handoff block unless there is material new evidence.
- If the issue is still the correct next task and development is safe, route to `development-agent` with a bounded implementation slice.
- If implementation is unsafe or blocked, record the exact blocker once and no-op until the blocker changes.
- If multiple automations or passes are only commenting on the issue, classify the process state as `YELLOW` and route to queue/lock hardening under issue `#39`.

The minimum useful development result is one completed issue or one explicit sub-slice with files changed, tests run, commit pushed, and issue state updated. Analysis-only passes are allowed for live safety and unclear authority, but they should not repeat unchanged conclusions.

## No-Op Rules

The controller should no-op when:

- state has not materially changed
- no safe task is unblocked
- active locks indicate another agent owns the next action
- live window exists but no safe intervention is needed
- docs are current enough and no scheduled health check is due

No-op is a valid successful controller pass.

For reviewability without noise, use `python tools/controller_queue.py ledger --outcome no_material_change` when the no-op explains why a high-priority issue was not advanced.
