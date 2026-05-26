# Live Postgame Learning Backlog

Status: foundation implemented; use for follow-up scoping
Created: 2026-05-26
Parent issue: #63
Completed foundation follow-ups: #78, #79

## Purpose

This backlog converts the 2026-05-20 through 2026-05-25 NBA/WNBA live testing lessons into tracked work before the next development cycle starts. It exists so this chat, automations, GitHub issues, repo docs, and Obsidian can share the same execution map.

The north star is not merely "make orders happen." Every live game must produce a complete learning object that shows which sleeve made money, which sleeve lost money, which blocker cost money, which signal was missed, and what the system should change before the next window.

## Source Authority

Postgame and development decisions must use this hierarchy:

1. Account-scoped direct CLOB fills and local Janus reconciliation.
2. Local Janus `portfolio.orders` / `portfolio.trades` lifecycle records.
3. Direct current-event open positions and open orders.
4. Direct CLOB token market tape for price path and fillability only.
5. Polymarket UI screenshots for operator audit and displayed rounding only.

`current_token_trades` and other token-level market-tape rows must not be reported as account PnL unless matched to known external order ids.

## Issue Routing

| Track | Owner issue | Purpose |
|---|---:|---|
| Postgame truth, PnL, replay, CLOB/UI grounding | #78 | Closed foundation for `postgame_evaluation.json`, replay comparison, source confidence, CLOB/UI grounding, missed-window/extrema analysis, and account-scoped PnL. |
| Sleeve portfolio, side/phase budgets, paired lifecycle | #79 | Closed foundation for budgeted sleeves, paired sell/rebuy, ultra-low, manual-imported positions, and local-vs-global blocker separation. |
| Exchange minimums, UI/CLOB behavior, market-order exception | #42 | Validate exact platform constraints and urgent profit-capture exception policy. |
| Profit-ratcheted risk ladder and development bankroll policy | #44 | Convert realized data into risk budget defaults and promotion gates. |
| Issue/task governance and tangent processing | #73 | Closed foundation ensuring new bugs/features become bounded tasks instead of repeated comments. |
| Obsidian backlog ingestion | #74 | Convert curated lessons into issue candidates without execution authority. |
| Pregame optional priors | #72 | Closed foundation keeping pregame research structured but non-authoritative. |
| Profile/future domains | #46/#47/#48 | Preserve future-domain hypotheses without preempting basketball runtime. |

## Development Tracks

### A. Truth And Accounting

- Separate account CLOB fills from public market tape everywhere.
- Add source confidence to every metric: `account_confirmed`, `db_confirmed`, `clob_market_tape`, `ui_observed`, or `inferred`.
- Build complete `postgame_evaluation.json` per event.
- Add realized PnL by game, side, sleeve, cycle, actor, and order.
- Add unresolved/missing evidence sections instead of pretending incomplete data is final.
- Add UI rounding comparison: exact CLOB price vs Polymarket UI displayed price.
- Add settlement/redeem status and final position lifecycle.

### B. Sleeve Architecture

- Make sleeves first-class budget owners, not labels after the fact.
- Add side-level budget policy: 50/50, favorite-heavy, underdog-heavy, winner-only, contrarian-only, selected-side, and adaptive split.
- Add phase-level budget policy: pregame, Q1, Q2, halftime, Q3, Q4, clutch, blowout, and ultra-low windows.
- Add or complete core-hold, grid/scalp, ultra-low rebound, controlled-fill, reduce/stop, rebuy, and manual-imported sleeves.
- Prevent generic global blockers from suppressing unrelated sleeves.

### C. Cycle And Order Behavior

- Every buy must declare its paired sell, stop, or hold reason.
- When a buy fills, immediately evaluate or create the paired sell.
- When a sell fills, immediately evaluate rebuy only if the sleeve policy allows it.
- Block repeated buys if no paired exit exists, unless explicitly core-hold.
- Add cycle PnL: buy leg, sell leg, rebuy leg, and final settlement.
- Add partial-fill handling.
- Add duplicate-cycle cooldown by sleeve, side, and band.
- Add end-of-game liquidation, settlement, or documented residual policy.

### D. Strategy Quality

- Backtest each sleeve alone.
- Backtest aggregate behavior.
- Backtest leave-one-out contribution.
- Add side-split optimizer: 50/50, 70/30, one-sided, delayed, and adaptive.
- Add phase optimizer: when the system should become more or less aggressive.
- Add ultra-low price policy for 0.5c-5c markets.
- Add high-price favorite policy for 85c-98c markets.
- Add volatility-band detector from observed price path.
- Add missed-window detection: "we should have bought here / sold here."
- Add strategy promotion and demotion rules from postgame evidence.

### E. Live Context

- Keep NBA and WNBA on the same execution platform, with only feed adapters differing.
- Improve scoreboard freshness and latency diagnostics.
- Add play-by-play latency tracking.
- Add player-status shocks: foul trouble, benching, injury, hot player run, ejection, and feed/status conflicts.
- Add score-run detector: 8-0, 10-2, 15-4, and configurable run windows.
- Add quarter-boundary strategy review.
- Add clutch-time mode.
- Add blowout-but-volatility mode, especially for ultra-low prices.

### F. Operator And Human Integration

- Import manual trades immediately.
- Attach manual trades to matched sleeves or `manual_imported` if no match exists.
- Let Janus manage manual positions with paired sell/rebuy/stop logic.
- Let the operator override side budget, sleeve budget, and phase mode through reviewed controls.
- Add a separate development-money risk profile from conservative production risk.
- Add live control intents such as `increase_aggression`, `pause_buys`, `exit_only`, and `ultra_low_mode`.

### G. Reporting And Learning

- Generate one complete postgame report per event.
- Generate a daily strategy scoreboard.
- Track per-sleeve ROI over time.
- Track per-sleeve max drawdown.
- Track missed profit from blocked signals.
- Track cost of bad blockers.
- Track bad trades caused by stale feeds.
- Track user/manual trades vs Janus trades.
- Add "what should change tomorrow" as a mandatory postgame section.
- Feed postgame recommendations into issue/task backlog automatically.

### H. Automation And CI/CD Loop

- Create or harden a postgame-review automation that cannot trade.
- Create a strategy-improvement automation that reads postgame reports and opens or updates tasks.
- Ensure GitHub issues have local task rows before recurring agents act.
- Prevent automations from commenting repeatedly without changing code, docs, tasks, artifacts, or blockers.
- Add regression fixtures from every live-game failure.
- Add CI tests for sleeve attribution and PnL accounting.
- Add replay tests for each promoted strategy.
- Add "do not promote unless postgame evidence passes" gates.

### I. Risk And Budget

- Define event budget as both percentage and nominal cap.
- Define sleeve budget inside event budget.
- Define max concurrent active cycles.
- Define max same-side exposure.
- Define max losing-side hold into final period.
- Define min expected edge after spread and slippage.
- Define when losing positions are learning-valid versus reckless.
- Make risk configurable per mode: `validation`, `development`, and `production`.

### J. Product And Observability

- Build a live event dashboard for score, CLOB, sleeves, cycles, blockers, fills, and PnL.
- Show "why no trade happened" per sleeve.
- Show "what Janus wants to do next."
- Show exact CLOB prices vs UI rounded prices.
- Show all open cycles and paired orders.
- Show live budget allocation.
- Show postgame replay comparison visually.

## Tangent Processing Rule

Any bug or feature discovered while implementing this backlog must be processed before broad coding continues:

1. If it is within the current issue's acceptance criteria, add or update a local task row in `automation/issue_task_register.md`.
2. If it changes scope, add a planned-backlog row or create a focused GitHub issue.
3. If it is live-money safety related, stop promotion and mark the blocker explicitly.
4. If it is future-domain work, route to #46, #47, or #48 and do not preempt #78/#79.
