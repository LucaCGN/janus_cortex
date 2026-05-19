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
| 2 | Active Janus-controlled live event, including stale or incomplete live evidence | `live-monitor-analyst` |
| 3 | Dirty tracked worktree exists without an active owning lock and no live safety issue outranks it | `development-end-phase` or `master-controller` cleanup |
| 4 | Closed event lacks postgame review or reconciliation | `postgame-reviewer` |
| 5 | Upcoming event lacks integrity gate | `pregame-integrity` |
| 6 | Upcoming event passed integrity but lacks plan/watchpoints | `pregame-planner` |
| 7 | Open unblocked sprint implementation issue exists and no live/pregame/postgame safety route outranks it | `development-agent` or `development-end-phase` |
| 8 | Backlog/issue taxonomy/queue missing or stale | `issue-backlog-manager` |
| 9 | Source-of-truth docs or Obsidian indexes stale | `docs-memory-agent` |
| 10 | Codex global portfolio management/scouting pass is due and no higher-priority live safety or NBA/WNBA readiness task is active | `codex-global-portfolio-agent` / `global-portfolio-agent` alias |
| 11 | New market/domain idea needs classification | `future-domain-research-agent` or `profile-research-agent` |
| 12 | No material state change | `master-controller` no-op |

Safety and live event state override backlog progress.

No-op is allowed only after open sprint issues have been checked for an actionable bounded slice. A clean worktree, clear queue, and unchanged runtime artifacts are not enough to no-op when an open P0/P1 implementation issue is unblocked.

## Recurring Automation Gate

If today's event data has not been reconciled through an explicit Codex/operator reconciliation pass, the controller may run only in docs/source-of-truth cleanup mode.

It must not enable recurring automation, mark live readiness green, or treat runtime handoffs as current until reconciliation is complete.

The 2026-05-18 bootstrap pass performed the first repo-local reconciliation and refreshed handoffs, but the operator still owns manual enablement of the recurring Codex app automation.

## Basketball Lifecycle Rules

| Lifecycle | Rule |
|---|---|
| `live` | No broad development. Monitor, reconcile, issue-create, run bounded game/market analysis for Janus infrastructure, or patch only critical failures. |
| `pregame` | Integrity precedes planning. Planning does not place orders. |
| `postgame` | Review/reconciliation precedes development planning unless a P0 safety bug blocks all work. |
| `settlement` | Direct CLOB/account truth must be reconciled before performance claims. |

Postgame direct-CLOB truth must be event-scoped before it is used for PnL attribution. Global account positions and open orders from alien, geopolitics, elections, AI-model, or other portfolio markets must not keep a completed NBA/WNBA event marked unresolved unless their token/condition/event scope matches that event.

If a completed Janus-covered NBA/WNBA event still has event-scoped direct CLOB open orders, open positions, valuation mismatches, or unresolved lifecycle state, route to the settlement/reconciliation issue for that event before enabling new live execution. For the 2026-05-18 Spurs/Thunder test, that issue is [#57](https://github.com/LucaCGN/janus_cortex/issues/57). Keep WNBA passive/shadow work in [#50](https://github.com/LucaCGN/janus_cortex/issues/50); do not use #50 as the NBA settlement bucket.

Issue [#50](https://github.com/LucaCGN/janus_cortex/issues/50) is strictly WNBA passive/shadow readiness. It should receive updates only for WNBA target resolution, WNBA passive CLOB capture, WNBA price-history/shadow summaries, WNBA blocker classification, or WNBA handoff/report changes. NBA live-monitor evidence, NBA settlement/redeem state, and global-portfolio execution gates belong to their own issues. Once a current #50 blocker report exists, repeated unchanged passive-shadow status should be recorded as a no-op ledger instead of another GitHub comment.

Resolved-market redemption is a settlement workflow, not CLOB order authority. A prior-event unredeemed position may be classified as a documented residual instead of live exposure only after fresh direct CLOB/account truth proves no event-scoped open orders, market resolution/payout state is known, expected residual value is recorded, and a ledger/follow-up issue owns the redeem or residual state. The app must continue to hold and operate with documented unredeemed residual positions, including zero-valued losing tokens, without treating them as active risk for unrelated new games. Non-dry-run redemption requires the gated Janus+Codex approval workflow in [#58](https://github.com/LucaCGN/janus_cortex/issues/58); never redeem from stale mirrors, screenshots, chat memory, or Obsidian notes.

After [#57](https://github.com/LucaCGN/janus_cortex/issues/57) is closed flat, [#58](https://github.com/LucaCGN/janus_cortex/issues/58) is an implementation issue, not a passive settlement-watch issue. If no active/near-term covered NBA/WNBA live-readiness route outranks development, the controller should claim one bounded #58 slice, starting with dry-run residual classification and redeem-preview tests. It should not wait for fresh Spurs/Thunder evidence or repeatedly compress as no-change solely because the old event remains flat.

## Active Live-Game Analyst Rules

When a covered NBA/WNBA game is live, the controller should not behave like a generic no-op scheduler. It should route to `live-monitor-analyst` and produce or inspect a fresh checkpoint that can support Janus decisions:

1. Freshness: prefer the newest machine-readable runtime artifact under `local/shared/artifacts/ops/<session_date>/`, plus the newest LLM-runtime artifact for the event. If the latest artifact predates the current phase, lacks direct current-event inventory, or conflicts with newer handoff evidence, run a bounded dry live-monitor/live-strategy checkpoint before reporting state.
2. Required live checkpoint fields: game status, period, clock, score, sampled orderbook bid/ask/spread, direct CLOB current-event open orders, open positions, recent fills/trades, pending intents, StrategyPlan gate, worker state, LLM/runtime trigger state, and live blockers.
3. Analyst output: summarize what changed in the game and market, what that implies for current Janus posture, whether any safety/strategy bug blocks monitoring, and the next safe action. This is allowed even when no orders are authorized.
4. No stale-flat summaries: do not say the event inventory is flat from memory or an older artifact if a newer direct-CLOB artifact shows open orders, fills, or positions. Direct CLOB evidence in the newest relevant artifact wins over automation memory, handoffs, GitHub comments, or screenshots.
5. If a live bug prevents fresh evidence generation, route to a critical live bug patch with the smallest file scope and focused tests. Do not defer to broad backlog development until live evidence is trustworthy.

## Sports Readiness Live-Test Rules

On an NBA/WNBA test day, repeated passive capture is not enough when the blocker is a missing covered-market StrategyPlanJSON:

1. If `current_plan_count_today=0` inside the pregame/live-monitor window for a Janus-covered NBA event, route to bounded pregame planning and submit one current StrategyPlanJSON before repeating passive-only captures.
2. A WNBA passive capture with `orders_allowed=false` is valid WNBA shadow evidence, but it does not satisfy NBA live-worker readiness or prove a covered-market order path.
3. Minimum-size live tests require explicit operator approval, direct CLOB/account truth, an active current StrategyPlanJSON, orderbook freshness, Janus integrity readiness, disabled raw exchange bypass, and Janus StrategyPlan/evaluate/execute authority.
4. After a minimum-size live order is submitted, immediately revise the current StrategyPlanJSON into post-order monitor-only mode with `shadow_only=true`, `entry_disabled=true`, and the live external order id. The next controller pass should monitor order status, live game state, target/stop/rebuy policy, and reconciliation evidence; it must not duplicate the buy.
5. At event start, Polymarket may clear/cancel resting orders. After start time, local submitted/open rows are advisory only: the controller must re-prove direct CLOB open orders or fills before treating a pregame buy or target as live. Missing direct CLOB orders after start should be classified as event-start expiry, not as pending exposure.
6. UUID catalog event ids must resolve through the catalog-linked NBA game id before live monitoring is considered complete. A live tick that reports `event_id_not_parseable` for a UUID covered event is a tooling blocker, not a trading signal.
7. Live-monitor artifacts must expose current-event inventory, not only worker readiness. If `live_execution_evidence.items` is empty because the worker is stopped, the controller still needs direct CLOB event inventory from the monitor artifact or a bounded dry live-strategy tick.
8. A prior final event with unresolved event-scoped direct CLOB inventory blocks new live-worker enablement until it is reconciled or explicitly classified as a documented residual. For current May 18 NBA state, [#57](https://github.com/LucaCGN/janus_cortex/issues/57) owns this gate; [#55](https://github.com/LucaCGN/janus_cortex/issues/55) remains entry-timing research, and [#50](https://github.com/LucaCGN/janus_cortex/issues/50) remains WNBA passive/shadow evidence.
9. If the only prior-event inventory is a resolved-market unredeemed residual with no direct open orders and no active fill ambiguity, route the redeem/residual tooling to [#58](https://github.com/LucaCGN/janus_cortex/issues/58) and do not block all new covered-market readiness solely on the unredeemed row.

## Global Portfolio Rules

The `janus-portfolio-manager` lane is the Codex global portfolio manager. It is active-management intent for the operator/global book, not a Janus NBA/WNBA trade validator, not the internal Janus covered-market portfolio/inventory agent, and not merely a read-only explorer:

1. Manage existing operator/global positions: verify direct CLOB truth, matching targets, stale/missing targets, exits, rebuy watches, and concentration risk.
2. Proactively scout uncovered categories such as geopolitics, economics, culture, crypto, and sports futures for trend-following opportunities where the thesis is trend, market structure, liquidity, mispricing, and return path rather than direct final-outcome prediction.
2a. Always check live basketball markets outside the Janus-covered NBA/WNBA modules when data is available. Until promoted, other basketball leagues belong to Codex global portfolio management, not the internal covered-market portfolio agent.
2b. Review ongoing events traded in the last month for 1c grid suitability. The first approved surface is preview-only `codex_tools/polymarket preview-grid-service`; high-frequency grid services require separate service-spawn approval, budget, kill-switch, ledger, rate-limit, and reconciliation gates.
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
- write locks are clear and tracked dirty paths are either absent or explicitly owned by the active lock
- tests/validation expectations are known
- live-order impact is explicit

Write locks are clear only when `python tools/controller_queue.py claim` succeeds for the selected issue and write scope. Duplicate active locks block the pass. Stale active locks are review blockers, not permission to overwrite. A dirty shared worktree before the claim blocks implementation unless the dirty files are already owned by the active claim.

If a controller pass observes dirty tracked files after a lock was released or with no active lock, the next action is cleanup, not new feature work. The cleanup pass must map files to issues, run relevant validation, and either commit/push coherent slices or record a concrete operator-review blocker. Repeated issue comments while such a dirty mixed scope exists are `YELLOW` process drift.

## Issue Progress Rules

The controller should prefer finishing useful issue-sized work over producing repeated status commentary.

When the same open issue remains the selected route across consecutive passes:

- Do not add another GitHub comment or full handoff block unless there is material new evidence.
- If the issue is still the correct next task and development is safe, route to `development-agent` with a bounded implementation slice.
- If implementation is unsafe or blocked, record the exact blocker once and no-op until the blocker changes.
- If multiple automations or passes are only commenting on the issue, classify the process state as `YELLOW` and route to queue/lock hardening under issue `#39`.
- If the comment history contains unrelated issue spillover, publish one blocker report and update/split issue state so the next pass has a single authoritative blocker. Do not continue adding mixed-domain comments to the overloaded issue.

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

Live-game exception: a live window is not a no-op merely because inventory did not change. If the game clock, period, score, orderbook, fills, LLM trigger state, or runtime evidence freshness changed, that is material live-monitor state. If none changed but the last checkpoint is stale or missing direct current-event inventory, generate a fresh checkpoint before no-op compression.

Dirty-worktree exception: a pass with dirty tracked files and no active owning lock is not a no-op. It must route to cleanup/review unless an urgent live safety issue requires a narrower intervention first.
