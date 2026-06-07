# Live Postgame Learning Backlog

Status: foundation implemented; use for follow-up scoping
Created: 2026-05-26
Parent issue: #63
Completed foundation follow-ups: #78, #79
Current live-window adoption: #63/JIT-63-13

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

2026-05-30 accounting correction: postgame reports must never equate gross buy turnover with invested capital or actual return. The canonical account return row separates gross buy turnover, sell proceeds, redeem proceeds, actual PnL, peak cash at risk, and return on peak cash at risk. Closed-position PnL from account-confirmed Polymarket data overrides partial activity/lifecycle cashflow when final settlement is not represented as a visible redeem row.

2026-05-31 May 30 live-money readback: `app/docs/reference/postgame_evaluation_2026-05-30_nba_wnba_live_window.md` is the current daily postgame source truth. The day finished account-positive at `+$6.616514` actual PnL on `$35.908342` peak cash at risk, but it exposed five development gaps that must drive the next loop: rebound-snipe sleeves distinct from micro-band scalping, manual-interference budget rebase, player-catalyst PBP/status shocks, duplicate signal pressure, and final cleanup/direct-lifecycle hardening. #83 remains closed for the gross-turnover accounting bug; direct-evidence timeout and lifecycle-row quality should route through focused #63/#82 follow-ups unless the account-return calculator regresses.

2026-05-31 Aces/Valkyries pre-tip watchpoint: `local/shared/reports/daily-live-validation/may31_aces_valkyries_flow_watchpoints.md` is the live evidence checklist for the 16:30 BRT WNBA window. The game should prove whether the current system actually uses DB/stat context, keeps winner-definition/core-hold, rebound-snipe, and grid/scalp sleeves independent, produces quarter-bound LLM/StrategyPlan reviews, treats triggers as order/revision opportunities rather than broad suppressors, exposes profit-ratcheted budget behavior, and preserves order-type-specific sizing semantics. Missing items now route to the focused GitHub issues listed in `app/docs/planning/current/final_system/backlog/janus_reliability_observability_development_plan_2026-06-01.md`.

2026-05-31 Aces/Valkyries postgame readback: `app/docs/reference/postgame_evaluation_2026-05-31_aces_valkyries_live_window.md` and runtime report `local/shared/reports/daily-live-validation/postgame_analysis_2026-05-31_aces_valkyries_flow_watchpoints.md` are the source-truth analysis for the operator pregame checklist. The game finished Las Vegas 91, Golden State 81 with live evidence present but no account-confirmed trades or PnL. The system proved a current eight-sleeve plan and quarter-trigger detection, but did not prove actual quarter-bound revision decisions, explicit DB-stat LLM context, filled parallel sleeve behavior, dollar-for-dollar profit budget, or order-type-specific minimum sizing. Timestamp-0 direct-account rows are now quarantined in live monitor/live tick, but postgame direct-event scope still needs the same trusted/untrusted split. Route follow-up through #84, #85, #86, #87, #88, #89, #90, #91, and #92.

## Issue Routing

| Track | Owner issue | Purpose |
|---|---:|---|
| Postgame truth, PnL, replay, CLOB/UI grounding | #78 | Closed foundation for `postgame_evaluation.json`, replay comparison, source confidence, CLOB/UI grounding, missed-window/extrema analysis, and account-scoped PnL. |
| Canonical live-event state and omniscience output | #92 | One live-event JSON snapshot/JSONL stream for score/feed, CLOB/account, worker, StrategyPlan, controls, LLM, signals, sleeves, budget, lifecycle, blockers, reconciliation, and artifact paths. |
| NBA scoreboard/event resolution diagnostics | #91 | Fix CLE/NYK-style scoreboard resolution blockers and expose candidate mapping evidence. |
| Postgame direct-trade trust policy | #84 | Extend timestamp-zero trusted/untrusted direct-trade splits into postgame direct-event scope. |
| Sleeve portfolio, side/phase budgets, paired lifecycle | #79 | Closed foundation for budgeted sleeves, paired sell/rebuy, ultra-low, manual-imported positions, and local-vs-global blocker separation. |
| Worker/runtime control fail-closed behavior | #85 | Prevent stale live-worker config from becoming restartable after API/service restarts. |
| Exchange minimums, UI/CLOB behavior, market-order exception | #42 / #86 | Validate exact platform constraints and implement order-type-specific limit-vs-market minimum sizing. |
| Profit-ratcheted risk ladder and development bankroll policy | #44 / #90 | Convert realized data into risk budget defaults and implement dollar-for-dollar account-confirmed profit add-ons. |
| Quarter-bound review and LLM revision evidence | #87 | Persist Q1, halftime/Q2, and Q3 StrategyPlan/LLM review rows with sleeve add/remove/keep decisions. |
| DB-stat and player/team context provenance | #88 | Make pregame/LLM/stat context usage explicit and freshness-checked. |
| Trigger/blocker semantics and scope cleanup | #89 | Ensure local blockers cannot suppress unrelated sleeves and postgame can score blocker cost by scope. |
| Account-confirmed postgame return correction | #83 | Closed route for preventing gross-turnover-as-loss regressions; future deeper replay or LLM gaps route to #80/#81/#44/#63 as applicable. |
| Issue/task governance and tangent processing | #73 | Closed foundation ensuring new bugs/features become bounded tasks instead of repeated comments. |
| Obsidian backlog ingestion | #74 | Convert curated lessons into issue candidates without execution authority. |
| Pregame optional priors | #72 | Closed foundation keeping pregame research structured but non-authoritative. |
| Profile/future domains | #46/#47/#48 | Preserve future-domain hypotheses without preempting basketball runtime. |

2026-05-26 adoption note: today's NBA worker uses the shared platform with six StrategyPlan sleeves across both sides: grid scalp, core hold, and opt-in ultra-low rebound. Ultra-low remains a development/validation sleeve and must be judged from postgame replay and account-scoped fills before default promotion.

2026-05-27 final readback note: the 2026-05-26 OKC/SAS game proved the runtime can run a six-sleeve plan and place Janus-gated live orders, but it did not prove the intended sleeve architecture. After four Spurs 5-share buys at `0.42`, Q4 generated zero intents despite repeated 1c-13c Spurs windows because the evaluator applied a token-wide `position_limit_reached` gate before sleeve-local budgets and cycles could act. Fix #63/JIT-63-12 before the next live window: explicit sleeve-scoped add-down strategies may add within local event/sleeve budgets while pending intents and live-safety/direct-truth gates remain strict blockers.

Session tracker: `local/shared/artifacts/dev-sessions/2026-05-27/nba_sas_okc_postgame_sleeve_refactor_session.md`. Postgame artifact: `local/shared/artifacts/ops/2026-05-26/postgame-review_20260527T035600Z.json`.

2026-05-27 P1/P2 postgame artifact note: `local/shared/artifacts/ops/2026-05-26/postgame-review_20260527T100457Z.json` is the current automation-consumable learning object. It adds `mode_comparison`, `sleeve_scoreboard`, `why_no_trade`, and `strategy_promotion_review` sections. Readback: realized known cashflow is `-$6.00` but final PnL is still review-gated; aggregate and isolated replay are `-$2.00`; all six sleeves have blockers; blocker scopes split into `global_gate=4080` and `local_sleeve=1521`; strategy promotion automation is intentionally disabled until unresolved lifecycle evidence and blocker repairs clear.

## Development Tracks

### A. Truth And Accounting

- Separate account CLOB fills from public market tape everywhere.
- Add source confidence to every metric: `account_confirmed`, `db_confirmed`, `clob_market_tape`, `ui_observed`, or `inferred`.
- Build complete `postgame_evaluation.json` per event.
- Add realized PnL by game, side, sleeve, cycle, actor, and order.
- Separate actual return from turnover: `gross_buy_turnover_usd` is activity volume, `peak_cash_at_risk_usd` is the invested-capital denominator, and `actual_pnl_usd` is sourced from account activity or closed positions.
- Add unresolved/missing evidence sections instead of pretending incomplete data is final.
- 2026-05-31 JIT-63-23 slice: order lifecycle reconciliation now emits `unresolved_lifecycle_reason` and `unresolved_lifecycle_reason_counts` so postgame rows distinguish direct-flat missing terminal status, unavailable direct account snapshots, and local open rows without external ids.
- Add UI rounding comparison: exact CLOB price vs Polymarket UI displayed price.
- Add settlement/redeem status and final position lifecycle.

### B. Sleeve Architecture

- Make sleeves first-class budget owners, not labels after the fact.
- Add side-level budget policy: 50/50, favorite-heavy, underdog-heavy, winner-only, contrarian-only, selected-side, and adaptive split.
- Add phase-level budget policy: pregame, Q1, Q2, halftime, Q3, Q4, clutch, blowout, and ultra-low windows.
- Add or complete core-hold, grid/scalp, ultra-low rebound, controlled-fill, reduce/stop, rebuy, and manual-imported sleeves.
- Add rebound-snipe sleeves: less frequent catalyst-aware entries after price dislocation, separate from micro-band scalping and manual-imported inventory.
- Prevent generic global blockers from suppressing unrelated sleeves.

### C. Cycle And Order Behavior

- Every buy must declare its paired sell, stop, or hold reason.
- When a buy fills, immediately evaluate or create the paired sell.
- When a sell fills, immediately evaluate rebuy only if the sleeve policy allows it.
- Block repeated buys if no paired exit exists, unless explicitly core-hold.
- Add cycle PnL: buy leg, sell leg, rebuy leg, and final settlement.
- Add partial-fill handling.
- Add duplicate-cycle cooldown by sleeve, side, and band.
- 2026-05-31 JIT-63-22 slice: aggregation duplicate cooldown now scopes by source, side, sleeve, band, and cycle; same-band budget claims emit explicit duplicate blocker evidence; distinct cycles and reviewed same-band multi-lot ladders remain eligible for parallel testing.
- Add end-of-game liquidation, settlement, or documented residual policy.
- 2026-05-31 JIT-82-03 slice: final settled reduce/stop rows now emit non-executable cleanup reason classes for direct positions, open orders, local targets, documented residuals, and blocked residual classifications; near-final live exits remain separate Janus-gated reduce candidates.

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
- Trigger a manual-interference rebase when operator/browser orders materially change inventory, open-order ladders, or budget state.
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
- 2026-05-31 JIT-44-04 slice: risk calibration now treats positive account-confirmed May 30 performance as input, not authority. Historical calibration reports risk-promotion blocker counts for unresolved lifecycle/final-cleanup/open-unrealized evidence, and live tick budget readback ignores positive realized PnL for profit-ratcheted addons unless the source is account/db confirmed and blockers are clean.

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
