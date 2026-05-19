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
| 9 | Codex global portfolio management/scouting pass is due and no higher-priority live safety or NBA/WNBA readiness task is active | `codex-global-portfolio-agent` / `global-portfolio-agent` alias |
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

## Sports Readiness Live-Test Rules

On an NBA/WNBA test day, repeated passive capture is not enough when the blocker is a missing covered-market StrategyPlanJSON:

1. If `current_plan_count_today=0` inside the pregame/live-monitor window for a Janus-covered NBA event, route to bounded pregame planning and submit one current StrategyPlanJSON before repeating passive-only captures.
2. A WNBA passive capture with `orders_allowed=false` is valid WNBA shadow evidence, but it does not satisfy NBA live-worker readiness or prove a covered-market order path.
3. Minimum-size live tests require explicit operator approval, direct CLOB/account truth, an active current StrategyPlanJSON, orderbook freshness, Janus integrity readiness, disabled raw exchange bypass, and Janus StrategyPlan/evaluate/execute authority.
4. After a minimum-size live order is submitted, immediately revise the current StrategyPlanJSON into post-order monitor-only mode with `shadow_only=true`, `entry_disabled=true`, and the live external order id. The next controller pass should monitor order status, live game state, target/stop/rebuy policy, and reconciliation evidence; it must not duplicate the buy.
5. UUID catalog event ids must resolve through the catalog-linked NBA game id before live monitoring is considered complete. A live tick that reports `event_id_not_parseable` for a UUID covered event is a tooling blocker, not a trading signal.

## Global Portfolio Rules

The `janus-portfolio-manager` lane is the Codex global portfolio manager. It is active-management intent for the operator/global book, not a Janus NBA/WNBA trade validator, not the internal Janus covered-market portfolio/inventory agent, and not merely a read-only explorer:

1. Manage existing operator/global positions: verify direct CLOB truth, matching targets, stale/missing targets, exits, rebuy watches, and concentration risk.
2. Proactively scout uncovered categories such as geopolitics, economics, culture, crypto, and sports futures for trend-following opportunities where the thesis is trend, market structure, liquidity, mispricing, and return path rather than direct final-outcome prediction.
3. Execute only through an approved Janus portfolio order-management path or an approved independent `codex_tools/polymarket/*` fallback path after all gates in `automation/global_portfolio_manager_contract.md` and `automation/codex_tooling_contract.md` are true.
4. If execution gates are missing, update watchlists, Obsidian lessons, GitHub blockers, and runtime evidence without preparing or submitting orders.
5. Successful new-market trades must become backlog tests or Obsidian do/don't lessons before any domain is promoted.
6. During NBA/WNBA test days, global-portfolio expansion is lower priority than sports readiness unless direct live-money safety is unclear.

Detailed global-portfolio automation rules live in `automation/global_portfolio_manager_contract.md`. Codex tool split and direct Polymarket fallback rules live in `automation/codex_tooling_contract.md`. The older explorer contract is retained as read-only discovery context.

## Covered-Market Portfolio Rules

The internal Janus covered-market portfolio agent is different from the Codex global portfolio manager:

1. It is associated with markets covered by the Janus trading Python system, currently NBA and WNBA.
2. It works through Janus DB/API state, StrategyPlanJSON inventory effects, direct CLOB/account reconciliation, Janus order-manager validators, and event review.
3. It does not scout uncovered geopolitics, economics, culture, crypto, or other future-domain opportunities.
4. It may run in parallel with Codex global portfolio work only when file/module/event/service/market locks are disjoint and no live-game safety window forbids development.

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
