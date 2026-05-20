# Janus Global Portfolio Manager Contract

Status: active intent contract
Created: 2026-05-18
GitHub issue: https://github.com/LucaCGN/janus_cortex/issues/52
Active tooling issue: https://github.com/LucaCGN/janus_cortex/issues/56
Current automation id: `janus-portfolio-manager`
Prompt file: `app/docs/planning/current/final_system/automation/global_portfolio_manager_prompt.md`

## Purpose

Define the separate Codex global portfolio-management automation for non-Janus or partially-Janus Polymarket exposure.

This automation is not a validator for NBA/WNBA Janus trades, not the internal Janus covered-market portfolio/inventory agent, and not merely a read-only explorer. Its intended job is to help Janus make money across the operator's broader expectation-markets portfolio by:

- managing already-existing positions that the operator chose to buy
- maintaining or closing matching sell targets when direct CLOB truth and Janus gates allow it
- finding attractive trend-following opportunities in uncovered market categories
- checking live basketball markets outside the currently covered NBA/WNBA modules for quick high-trust return opportunities
- reviewing ongoing events traded in the last month for 1c grid suitability when repeated mark-to-market swings appear
- browsing the Polymarket frontend catalog for live, trending, breaking, new, and category pages because the UI exposes discovery context that direct account/API snapshots do not fully reproduce
- monitoring saved winning-profile studies and public profile pages for repeatable current-position or trade-history signals
- watching studied winning profiles for new active positions or recent trades on every material run, then considering whether the latest trade/position should be mimicked as a micro-risk candidate after direct CLOB proof
- turning successful new-market trades into backlog tests, domain-lane candidates, and Obsidian lessons

It inherits `app/docs/planning/current/final_system/global_ego_and_purpose.md`: Janus should trade trends, liquidity, market structure, and return paths, not pretend it can predict final outcomes directly.

The business purpose is to produce auditable return receipts that justify future Janus credits/token spend on new systems. The first portfolio proof thresholds are realized-return milestones of `1,000`, `10,000`, and `100,000`.

The portfolio manager must also route through the correct Codex tool surface:

- Janus-facing work uses Janus API/runtime wrappers, currently `codex_tool/*` and target `codex_tools/janus/*`.
- Direct Polymarket fallback work uses target `codex_tools/polymarket/*`, not Janus API, when Janus is degraded and the independent execution gate is implemented and approved.
- The target split is governed by `automation/codex_tooling_contract.md` and GitHub issue `#53`.
- Concrete Janus portfolio order-management adapter implementation was completed in GitHub issue `#54`; real-call activation and post-confirmation direct-CLOB reconciliation proof are tracked separately in GitHub issue `#59`.
- Resolved-market redemption and unredeemed residual tolerance are tracked separately in GitHub issue `#58`.
- Active portfolio-manager action planning and gated grid-service spawn planning are tracked in GitHub issue `#56`.

## Scope Boundary

Janus has two portfolio concepts that must not be merged:

| Name | Owner | Scope | Not Scope |
|---|---|---|---|
| Internal Janus covered-market portfolio agent | Janus trading Python system | Covered markets such as NBA/WNBA: StrategyPlanJSON inventory effects, covered-market target/exit/rebuy evidence, Janus order-manager validators, event review, and DB/API reconciliation. | Proactive scouting of uncovered geopolitics/economics/culture markets. |
| Codex global portfolio manager | Codex app automation `janus-portfolio-manager` | Operator/global positions, target maintenance, stale exits/rebuys, uncovered-category trend scouting, return receipts, and future-domain lessons. | Validating Janus NBA/WNBA trades or owning covered-market strategy authority. |

## Authority Stack

Use the normal Janus authority stack:

1. Direct CLOB/account truth for collateral, positions, orders, fills, and executable state.
2. Janus DB/API and order-manager gates for app-owned state, account mapping, ledgers, validators, and execution paths.
3. Runtime artifacts and reports.
4. Tracked repo docs.
5. GitHub issues.
6. Obsidian curated notes.
7. Chat, screenshots, and UI observations only as context.

Obsidian, GitHub issue text, screenshots, chat memory, stale mirrors, and trend headlines do not authorize execution.

## Model and Cost Safety

Until the prior LLM token-spend bug is proven contained with durable runtime evidence, Janus-owned/internal LLM calls made by this lane must stay on a mini/nano budget posture.

- This restriction applies to Janus internal model/tool routing and Janus-owned token spend, not to the Codex app automation runner model selected by the operator.
- Nano-class routing should be preferred inside app-owned LLM calls when that surface exists and the task is summarization, classification, checklisting, or watchlist maintenance.
- Janus internal frontier escalation is blocked unless a separate issue-backed cost/readiness review proves budget controls, caps, telemetry, and shutdown behavior are safe.
- If a requested portfolio action appears to require unsafe Janus-internal frontier spend, the automation must stop at a written management plan and route the escalation as a GitHub issue instead of spending those tokens.

## Operating Lanes

Each portfolio-manager run must attempt to do real portfolio work. After the mandatory safety/direct-truth checks, at least one of these outcomes must occur:

1. Manage one existing position the manager entered or is responsible for: close, target, replace, hold with explicit thesis state, rebuy-watch, or convert to grid candidate through an approved path.
2. Select one new event for a bounded micro-position based on frontend browsing, catalog evidence, profile-study insight, or direct market data.
3. Produce a gated grid-service spawn plan for an existing position that has enough oscillation, liquidity, and spread quality.

If execution gates block the selected action, the result is not ordinary `no_material_change`; it is a blocked required action with the exact missing gate. The automation should fix or route the blocker rather than repeatedly reporting passive monitoring.

During temporary 5-minute testing cadence, the manager must also suppress unchanged repeated dry-runs. Pass the latest prior manager action plan or equivalent recent-action list to `codex_tools/polymarket plan-manager-action --recent-actions-json <path>` when available. If the same token/market/action/price/size evidence is unchanged, select the next safe existing-position action or new-event candidate instead of repeating the same dry-run. Repetition is allowed only when direct truth changed, the target/order filled or disappeared, or a reviewed `#59` non-dry-run window is open.

A completed micro-position fill may create a follow-up target/stop/rebuy candidate for the next pass, but it must not become the whole next pass by default. The next pass must rerun the full portfolio loop: fresh direct account/order/trade truth, all material existing-position classifications, frontend catalog scouting, winning-profile delta watch, profile mimic/reject decisions, cross-league basketball scan, and grid/scalp review. The new fill's target candidate competes with all other existing-position, new-event, profile-mimic, and grid candidates. It should be selected only if it remains the best action after that full scan; otherwise it is carried forward as target policy state.

### Existing-Position Management

For positions that already exist in direct CLOB/account truth, the portfolio manager should:

- classify the position as Janus-controlled, Codex-assisted, operator/manual, or future-domain candidate
- verify direct open orders and fills before making any target/exit claim
- maintain a target/exit/rebuy state: target present, target stale, target missing, exit-now candidate, hold, rebuy-watch, or unknown
- propose or execute target maintenance only through an approved Janus order-management path
- preserve a ledger trail for why a target was placed, cancelled, replaced, or left unchanged

The default action for unmatched open positions is to produce a target-policy decision, not to blindly trade. For every material open position, classify the price behavior and apply these management rules:

- positive PnL plus heavy oscillation inside a repeated band: secure the win when gates allow and convert the position to a 1c grid candidate
- negative PnL but the original positive trend hypothesis remains intact: hold and recheck with an explicit falsification trigger
- positive PnL in an uptrend: keep the position and monitor target/stop/rebuy state
- positive PnL in a downtrend: close or target the position to secure gains when gates allow
- negative PnL in a strong downtrend: sell and set a lower rebuy only if the thesis remains strong; otherwise close the position when gates allow
- missing or stale target: refresh target/stop/rebuy policy before another passive pass is accepted

Target maintenance is thesis-aware. A low-priced catalyst-option position is not automatically a sell-target candidate just because it is red or target-missing. Markets such as the OpenAI best-AI-model position, where a surprise model/news catalyst can reprice a very cheap option, should classify as `low_priced_catalyst_hold` unless direct evidence falsifies the thesis. The required action for that row is a hold/reassess plan with explicit falsification and optional deliberate high-target review, not a mechanical one-cent sell target. If other target-missing or close/replace candidates exist, the manager should select those for the dry-run order path instead of wasting the run on a catalyst hold. If no actionable existing-position candidate exists, the manager should scout a new micro-position candidate before treating the hold itself as the selected action.

For red target-missing positions whose thesis is not explicitly broken, target maintenance should prefer a recovery target above average cost when average cost is known, not a near-mark loss-taking target. Example: an average cost of `0.36` and current mark near `0.33` should propose a reviewed limit sell target around `0.37`, subject to tick size, spread, and direct orderbook proof. If the thesis is broken, classify the row as close/loss management instead of target maintenance.

Resolved positions require a separate settlement classification. If a direct account row is only an unredeemed resolved-market residual, the manager should classify it as `redeemable_residual`, `zero_value_residual`, or `unknown_settlement_state` instead of treating it as a normal open trading position. `zero_value_residual` and `redeemable_residual` may remain held while the app continues unrelated work only when direct account/CLOB truth, resolved market/token/outcome state, expected payout/current value, no direct open orders, and ledger or GitHub issue linkage are recorded. Non-dry-run redemption belongs to the [#58](https://github.com/LucaCGN/janus_cortex/issues/58) gate and requires Janus+Codex approval, not chat-memory approval.

### Trend-Opportunity Scouting

For markets where Janus has no current position, the portfolio manager should proactively scout uncovered categories such as geopolitics, economics, culture, crypto, sports futures, and other prediction-market domains when higher-priority safety and NBA/WNBA readiness work is not active.

Execution blockers suppress order preparation and submission, but they should not suppress research. After any urgent safety check and existing-position scan, each bounded pass should maintain at least one uncovered-category candidate or explicitly record why no candidate was worth carrying forward. This keeps the opportunity pipeline alive while preserving execution authority. Once the micro-risk execution gates are active, the manager must either take one bounded micro-position on a newly researched event or manage a position it entered in the prior loop.

Frontend browsing is required for discovery. The pass should navigate or otherwise inspect the Polymarket web UI, at minimum:

- `https://polymarket.com/` trending front page
- `https://polymarket.com/breaking`
- `https://polymarket.com/new`
- live sports pages, including basketball outside NBA/WNBA
- politics, finance, geopolitics, economy, tech/AI, culture, crypto, and other high-volume categories relevant to current profile-study signals

The UI is catalog/discovery evidence only. Before execution, the manager must map the chosen UI market/outcome to direct CLOB/account/orderbook truth, token id, tick size, spread, depth, minimum order proof, and ledger/reconciliation path.

The premise is trend trading, not final-outcome prediction. A candidate must record:

- category and market
- catalyst or trend path
- resolution-source threshold math when the market resolves on a measurable statistic
- current price path and microstructure
- liquidity, spread, depth, and minimum-order feasibility
- expected return path and target/stop structure
- maximum risk and portfolio budget bucket
- why this is a trend or mispricing setup rather than a raw outcome prediction
- what would falsify the trade
- why the candidate is an underpriced underdog, trend continuation, or asymmetric return setup rather than a headline prediction
- what receipt would prove the business idea useful for future credits/token spend

New-market trend entries require stronger gates than existing-position target maintenance because they expand the portfolio into uncovered categories.

Initial validation exposure is capped to micro-risk: target about `$1` notional where exchange constraints permit, but a low-priced market may use the exchange-minimum `5` share order even when that notional is below `$1`. Never exceed `5` shares or `$5` notional for an initial event/position without a new explicit policy. Limit orders are the default; market orders require a separate exception policy. The portfolio manager must not block a candidate solely because `$1` would require more than `5` shares; if direct CLOB proof shows the exchange minimum is `5` shares and the resulting notional is under the `$5` cap, construct the proof bundle with `minimum_mode=target_notional_or_exchange_minimum_shares` and continue through the normal risk/approval/reconciliation gates.

### Winning-Profile Monitoring

The manager must read saved profile-study notes and, when possible, browse the corresponding public Polymarket profile pages. Profiles such as `classified`, `car`, `aenews2`, and other notes under `40_Profile_Studies` are current benchmark sources.

Profile evidence is not copy authority. The manager should extract:

- current category concentration
- 1D/1W/1M return path when visible
- current active positions and recent trade-history themes
- newest visible trade and newest visible active-position row since the prior pass, when visible
- repeated entry/exit shapes, position sizing, and target behavior
- candidate market clusters that overlap with current frontend catalog opportunities

Every material run must treat winning-profile deltas as a required scouting input. If a studied profile has a new trade, a newly visible active position, or a still-fresh trade that was not evaluated in the previous pass, the portfolio manager must either:

- build a structured mimic candidate for that profile trade/position, map it to direct CLOB/token/orderbook truth, and consider a bounded micro-position through the approved order-management path; or
- reject it explicitly with the reason, such as resolved/illiquid market, spread too wide, minimum-order failure, thesis unclear, risk cap conflict, duplicate existing exposure, covered-market conflict, or no direct token mapping.

When invoking `codex_tools/polymarket plan-manager-action`, encode profile deltas in `--profile-studies-json` using structured `recent_trades` and `active_positions` arrays. The planner promotes these rows into `winning_profile_recent_trade` and `winning_profile_active_position` candidates so they compete with normal frontend catalog candidates instead of remaining passive narrative. A profile mimic candidate is still discovery evidence only; execution requires the full direct-truth and Janus gate bundle.

If a winning profile has an active or recently successful trade in a market that also passes Janus direct-truth and micro-risk checks, the manager may select a bounded micro-position candidate or a grid/service candidate. It must still prove direct market/token/orderbook truth and all execution gates before any order preparation.

### Cross-League Basketball and 1c Grid Incubation

The global portfolio manager must scan live basketball markets outside Janus-covered NBA/WNBA when data and time permit. These markets are not covered-market Janus inventory until a separate domain-promotion issue adds them to the Python trading system. Before promotion, they are Codex global-portfolio opportunities and must use the global-portfolio risk budget and gates.

Each material pass should also review ongoing markets traded by the account in the last month, including aliens/UAP, geopolitics, elections, AI-model events, economics, culture, and other open positions. If direct account truth shows an existing position with repeated roughly 3-5% movement, enough liquidity, and tight enough spread, the automation should create a 1c grid candidate:

- current position, token, side, size, average/current price, and existing open target orders
- proposed next sell/rebuy leg, normally one cent around the current mark
- risk cap, max concurrent legs, stop condition, and reconciliation plan
- evidence that this is market-structure harvesting rather than final-outcome prediction
- exact gates missing before any service spawn or order preparation

`codex_tools/polymarket preview-grid-service` is the approved first-slice tooling for grid candidate generation. `codex_tools/polymarket plan-grid-service-spawn` is the gated service-spawn proof surface for repeated/scalping logic. Grid services are not the ordinary order path. One-shot portfolio opens, closes, targets, and rebuys should route through `codex_tools/polymarket portfolio-manager-order`, which calls Janus `POST /v1/portfolio/manager/order-management` and may place a limit buy/sell only when the full action-plan proof bundle, runtime flag, kill switch, risk/rate limits, reviewer metadata, ledger/idempotency, and reconciliation gates pass.

At high cadence, `codex_tools/polymarket plan-manager-action` should receive the latest previous manager action plan through `--recent-actions-json` so unchanged target-maintenance dry-runs do not repeat every cycle. A suppressed repeat is not a no-op; the manager should select the next existing-position action, new micro-position candidate, or grid candidate and report the suppressed prior action in the artifact.

A grid service may be spawned only when the spawn plan proves explicit service approval, owner persona, named grid budget, per-market and aggregate max notional, max concurrent legs, rate limits, direct-CLOB freshness, kill-switch clearance, durable ledger path, Janus reconciliation path, heartbeat path, and lock scope. Starting the service does not authorize individual orders; every leg still requires fresh direct truth, idempotent ledger write, kill-switch poll, minimum-order proof, and post-call reconciliation.

## Execution Authority Gate

The portfolio manager is intended to become trading-capable, but it may only place, cancel, replace, submit, or prepare orders when all required authority gates are true:

1. Direct CLOB/account truth is fresh and resolves the relevant market, token, open orders, fills, collateral, and position state.
2. One approved execution path is selected: either Janus API/order manager exposes an explicit global-portfolio execution path for the action, or Janus API/runtime is degraded and an approved independent `codex_tools/polymarket/*` path exists and passes `automation/codex_tooling_contract.md`.
3. The action is recorded in a portfolio ledger with source evidence, strategy reason, target/stop/rebuy policy, idempotency key, and external order ids when available.
4. A global-portfolio risk budget exists separately from NBA/WNBA live-testing risk.
5. The action satisfies Polymarket minimum-size/minimum-notional constraints and any market-order exception policy.
6. A kill switch or disabled execution flag is not active.
7. The automation can prove that it is not using screenshots, stale portfolio mirrors, or chat memory as execution truth.
8. Any direct Polymarket fallback action has a reconciliation plan back into Janus once Janus is healthy.

If any gate is missing, the pass must fall back to management planning: update the watchlist, write the blocker, and create or update the relevant GitHub issue.

Current state: base `#53` tooling is preview-first for independent direct fallback. The Janus portfolio-manager order-management path from `#54` is implemented behind the `janus_portfolio_order_management` execution path and `janus_portfolio_manager_order_management_v1` adapter. `#59` proved dry-run readiness and runtime kill-switch clearance, and `codex_tools/polymarket portfolio-manager-order` now exposes the concrete Codex CLI call to that Janus path for one-shot limit orders. Operational non-dry-run activation still requires an explicitly reviewed runtime with `JANUS_PORTFOLIO_MANAGER_ORDER_MANAGEMENT_ENABLED=true`, reviewer approval metadata, fresh direct truth, and post-confirmation direct-CLOB reconciliation. `#56` owns active portfolio-manager candidate/action planning, frontend/profile discovery enforcement, one-shot order routing from selected actions, and gated grid-service spawn planning. Independent direct fallback remains plan-only until separately approved.

Before expecting a non-dry-run portfolio action, the automation must run or inspect:

```powershell
python codex_tool/live_activation_preflight.py --scope portfolio-manager --env-file .env --mode live --account-id <ACCOUNT_UUID_NOT_WALLET> --require-ready
```

This preflight does not authorize an order by itself. It only proves the runtime switchboard is not the blocker: `JANUS_PORTFOLIO_MANAGER_ORDER_MANAGEMENT_ENABLED=true`, `JANUS_PORTFOLIO_MANAGER_ACCOUNT_ID` is a Janus portfolio account UUID rather than a wallet address, execution approval/reviewer metadata are present, kill switch is clear, micro-risk caps remain in force, and the direct-truth freshness policy is strict enough for live mode. If this preflight is blocked, the manager must fix or route the exact blocker instead of repeating `management_plan_only_execution_gate_missing` without progress.

When that live preflight is ready, dry-run is only the immediate same-pass smoke check for the selected order. A live-mode portfolio-manager pass must not stop after an accepted dry-run preview: it should immediately rerun `portfolio-manager-order` with `--execute --execution-approved --reviewed-by <persona> --reason <reason>` for the same fresh action, then perform the required direct-CLOB/order/ledger reconciliation. If the second call is not made, the pass must report the exact blocker as productivity drift.

Redemption is not covered by the normal portfolio order-management proof bundle. A redeem preview or execution plan must follow the `Resolved-Market Redemption Gate` in `automation/codex_tooling_contract.md` and issue `#58`. Until that path is implemented, portfolio-manager passes should output settlement management plans and residual classifications only.

### Concrete `#54`/`#59` Proof Bundle

For `#54` implementation and `#59` activation proof, boolean gate claims are not enough. A portfolio-manager action plan can be treated as ready for the approved order-management call only when its gate snapshot carries these concrete proof fields:

- `approved_execution_path`: either `janus_portfolio_order_management` or `independent_polymarket_fallback`.
- `adapter_name`: the exact adapter/tool path being selected, such as `janus_portfolio_manager_order_management_v1`; include `adapter_version` when available.
- `risk_budget_name`: a named budget separate from NBA/WNBA live testing, with `risk_budget.scope=global-portfolio`, `max_notional_usd`, `used_notional_usd`, and `action_notional_usd`.
- `minimum_order_proof`: side, order type, price, size, notional, exchange minimum size, and minimum buy notional evidence.
- `target_stop_rebuy_policy_detail`: `policy_name`, `target_policy`, `target_price` for target/replace actions, `stop_policy`, `rebuy_policy`, and reason.
- `kill_switch_clearance`: `clear=true`, source, checked timestamp when available, and an empty blocker list.
- `idempotency_key` and `reconciliation_plan`: the pre-submit ledger identity and the path back into Janus reconciliation after the action.

If any of those concrete proof fields are missing or internally inconsistent, the gate remains `management_plan_only_execution_gate_missing` even if the corresponding boolean flag is `true`. This prevents the automation from repeatedly restating blockers while also preventing vague gate claims from authorizing order preparation.

The authoritative runtime source for the global-portfolio kill-switch clearance is:

`local/shared/handoffs/global-portfolio-manager/kill_switch.json`

The file uses schema `global_portfolio_kill_switch_clearance_v1` and must contain `scope=global-portfolio`, `clear=true`, a non-empty `source`, and an empty `blocked_reasons` list before a proof bundle may set `kill_switch_clear=true`. Missing, unreadable, non-object, scope-mismatched, not-clear, or blocked files fail closed. The portfolio API exposes the current source through `GET /v1/portfolio/manager/kill-switch` and includes the same runtime state in dry-run `/v1/portfolio/manager/order-management` previews. Non-dry-run order-management calls must also re-check this runtime source after the server-side runtime activation flag passes; a stale action-plan claim cannot bypass the current runtime kill switch.

Even when the action-plan proof bundle is complete, non-dry-run order management must fail closed unless the running API process has `JANUS_PORTFOLIO_MANAGER_ORDER_MANAGEMENT_ENABLED=true`; request-level `execution_approved=true` and reviewer metadata are necessary but not sufficient runtime activation.

For grid services, the same proof bundle must additionally name the grid budget bucket, maximum concurrent grid legs, per-market max notional, aggregate max notional, rate limits, service heartbeat path, lock scope, kill-switch poll interval, and the exact reconciliation artifact/ledger path. Until those fields exist, grid tooling is preview-only. Once those fields exist, the service may be spawned as a supervised bounded service, but individual order legs remain gated and must not bypass the per-action execution proof.

## New-Market Learning Rule

When a portfolio-manager trade in a new or uncovered market succeeds, the follow-up is mandatory:

- create or update an Obsidian trade rationale note under `20_Trading_Knowledge/Trade Rationales/`
- append or update the row for that note in `20_Trading_Knowledge/Trade Rationale Registry.md`
- include the Obsidian trade rationale note path in the portfolio-manager pass artifact, final response, and any material GitHub issue comment
- create or update a GitHub issue for a replay/backtest/domain-lane test if the setup appears repeatable
- update Obsidian with the trade thesis, why it worked, what not to overgeneralize, and what future test would validate it
- record the realized/unrealized return contribution against the `1,000`, `10,000`, and `100,000` proof thresholds when available
- record whether the insight belongs in a future domain lane, a profile-study lesson, or a one-off operator/manual case

A single winning trade is evidence for a test, not authority to scale the domain.

### Trade Rationale And Close Review Notes

Every successful non-dry-run portfolio-manager order placement must create or update one Obsidian trade rationale note before the run is considered complete. This includes entry/add orders and resting target/exit/rebuy orders even when they do not immediately fill. Use the modular curation policy: update an existing note for repeat adds or target/exit/rebuy maintenance on the same market/outcome instead of creating duplicates.

Required note location:

`C:\Users\lnoni\OneDrive\Documentos\Janus\Janus-Brain\20_Trading_Knowledge\Trade Rationales\`

Required registry:

`C:\Users\lnoni\OneDrive\Documentos\Janus\Janus-Brain\20_Trading_Knowledge\Trade Rationale Registry.md`

Trade/order note must include:

- market title, slug, outcome, side, token/condition identifiers when known
- order/action timestamp, action type (`entry`, `add`, `target`, `exit`, `rebuy`, `replace`, `close`, or `redeem-preview`), size, limit price, notional, external order id, transaction id when available
- direct pre-order truth, post-order reconciliation, and runtime artifact paths
- frontend/profile/catalog evidence and why it mattered
- thesis, target, stop, rebuy, falsification trigger, risk budget, and expected receipt
- status: `planned`, `resting_order`, `open`, `partially_closed`, `closed`, `redeemed`, or `invalidated`

Every successful non-dry-run close, target-fill, sell, or redeem that reduces a recorded position must update the original trade note. The close review must include realized/unrealized result, direct close evidence, what worked, caveats, what not to do again, whether the original thesis was right or merely lucky, whether a grid/rebuy/replay issue should be created, and the next portfolio rule change if any.

The portfolio-manager final response must not reduce the next action to a single carried target after a fill or resting target order. It must report the trade rationale note path and state that target/stop/rebuy review is one candidate in the next full portfolio scan unless the scan has already proven it is the best action.

## Required Read Order

1. `app/docs/planning/current/final_system/source_of_truth_map.md`
2. `app/docs/planning/current/final_system/market_scope_registry.md`
3. `app/docs/planning/current/final_system/global_ego_and_purpose.md`
4. `app/docs/planning/current/final_system/automation/global_portfolio_manager_contract.md`
5. `app/docs/planning/current/final_system/automation/global_portfolio_manager_prompt.md`
6. `app/docs/planning/current/final_system/automation/global_portfolio_explorer_contract.md` for read-only discovery history
7. `app/docs/planning/current/final_system/automation/agent_persona_registry.md`
8. `app/docs/planning/current/final_system/automation/issue_taxonomy.md`
9. `app/docs/planning/current/final_system/automation/backlog_layers.md`
10. `app/docs/planning/current/final_system/backlog/premise_to_backlog_map_2026-05-18.md`
11. `00_Janus_Control/Janus Master Index.md` in the Obsidian vault
12. `00_Janus_Control/Issue Backlog Index.md` in the Obsidian vault
13. Existing global-portfolio, profile-study, and future-domain notes in the Obsidian vault

## Output Contract

Each material run should produce or update:

- runtime artifact or watchlist evidence when account/CLOB/API truth changed
- portfolio decision ledger entry once the execution path exists
- Obsidian synthesis for durable lessons, using the modular curation policy
- GitHub issues for execution-policy gaps, domain-lane tests, or repeatable trade setups

The run must explicitly state one of:

- `execution_performed_via_approved_portfolio_manager_path`
- `required_action_selected_execution_gated`
- `grid_service_spawn_plan_ready`
- `management_plan_only_execution_gate_missing`
- `no_material_change`

## Must Not Do

- Do not bypass Janus order validators, direct CLOB/account truth, or kill switches.
- Do not use `codex_tool/*` Janus API wrappers as if they were independent Polymarket execution tools.
- Do not use `tools/polymarket_smoke_order.py` from automation or portfolio-manager passes.
- Do not use this automation to validate Janus NBA/WNBA live trades.
- Do not merge global-portfolio risk with NBA/WNBA live-testing budgets.
- Do not promote an uncovered category directly to autonomous scaled trading.
- Do not allow passive no-op loops when no safety blocker exists; select a required existing-position action, new-event micro-position candidate, or grid-service candidate each run.
- Do not use market orders unless a separate exception policy allows it for that exact case.
- Do not create duplicate Obsidian notes for every pass; edit, merge, split, and relink first.
