# Janus Global Portfolio Manager Contract

Status: active intent contract
Created: 2026-05-18
GitHub issue: https://github.com/LucaCGN/janus_cortex/issues/52
Current automation id: `janus-portfolio-manager`
Prompt file: `app/docs/planning/current/final_system/automation/global_portfolio_manager_prompt.md`

## Purpose

Define the separate Codex global portfolio-management automation for non-Janus or partially-Janus Polymarket exposure.

This automation is not a validator for NBA/WNBA Janus trades, not the internal Janus covered-market portfolio/inventory agent, and not merely a read-only explorer. Its intended job is to help Janus make money across the operator's broader expectation-markets portfolio by:

- managing already-existing positions that the operator chose to buy
- maintaining or closing matching sell targets when direct CLOB truth and Janus gates allow it
- finding attractive trend-following opportunities in uncovered market categories
- turning successful new-market trades into backlog tests, domain-lane candidates, and Obsidian lessons

It inherits `app/docs/planning/current/final_system/global_ego_and_purpose.md`: Janus should trade trends, liquidity, market structure, and return paths, not pretend it can predict final outcomes directly.

The business purpose is to produce auditable return receipts that justify future Janus credits/token spend on new systems. The first portfolio proof thresholds are realized-return milestones of `1,000`, `10,000`, and `100,000`.

The portfolio manager must also route through the correct Codex tool surface:

- Janus-facing work uses Janus API/runtime wrappers, currently `codex_tool/*` and target `codex_tools/janus/*`.
- Direct Polymarket fallback work uses target `codex_tools/polymarket/*`, not Janus API, when Janus is degraded and the independent execution gate is implemented and approved.
- The target split is governed by `automation/codex_tooling_contract.md` and GitHub issue `#53`.
- Concrete non-dry-run portfolio execution beyond preview/ledger planning is tracked separately in GitHub issue `#54`.

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

### Existing-Position Management

For positions that already exist in direct CLOB/account truth, the portfolio manager should:

- classify the position as Janus-controlled, Codex-assisted, operator/manual, or future-domain candidate
- verify direct open orders and fills before making any target/exit claim
- maintain a target/exit/rebuy state: target present, target stale, target missing, exit-now candidate, hold, rebuy-watch, or unknown
- propose or execute target maintenance only through an approved Janus order-management path
- preserve a ledger trail for why a target was placed, cancelled, replaced, or left unchanged

The default action for unmatched open positions is to produce a target-policy decision, not to blindly trade.

### Trend-Opportunity Scouting

For markets where Janus has no current position, the portfolio manager should proactively scout uncovered categories such as geopolitics, economics, culture, crypto, sports futures, and other prediction-market domains when higher-priority safety and NBA/WNBA readiness work is not active.

The premise is trend trading, not final-outcome prediction. A candidate must record:

- category and market
- catalyst or trend path
- current price path and microstructure
- liquidity, spread, depth, and minimum-order feasibility
- expected return path and target/stop structure
- maximum risk and portfolio budget bucket
- why this is a trend or mispricing setup rather than a raw outcome prediction
- what would falsify the trade
- why the candidate is an underpriced underdog, trend continuation, or asymmetric return setup rather than a headline prediction
- what receipt would prove the business idea useful for future credits/token spend

New-market trend entries require stronger gates than existing-position target maintenance because they expand the portfolio into uncovered categories.

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

Current state: base `#53` tooling is preview-first and non-executing. Concrete non-dry-run execution is tracked by `#54`; until that issue passes, direct fallback remains plan-only or dry-run preview-only.

### Concrete `#54` Proof Bundle

For `#54`, boolean gate claims are not enough. A portfolio-manager action plan can be treated as ready for the approved order-management preview only when its gate snapshot carries these concrete proof fields:

- `approved_execution_path`: either `janus_portfolio_order_management` or `independent_polymarket_fallback`.
- `adapter_name`: the exact adapter/tool path being selected, such as `janus_portfolio_manager_order_management_v1`; include `adapter_version` when available.
- `risk_budget_name`: a named budget separate from NBA/WNBA live testing, with `risk_budget.scope=global-portfolio`, `max_notional_usd`, `used_notional_usd`, and `action_notional_usd`.
- `minimum_order_proof`: side, order type, price, size, notional, exchange minimum size, and minimum buy notional evidence.
- `target_stop_rebuy_policy_detail`: `policy_name`, `target_policy`, `target_price` for target/replace actions, `stop_policy`, `rebuy_policy`, and reason.
- `kill_switch_clearance`: `clear=true`, source, checked timestamp when available, and an empty blocker list.
- `idempotency_key` and `reconciliation_plan`: the pre-submit ledger identity and the path back into Janus reconciliation after the action.

If any of those concrete proof fields are missing or internally inconsistent, the gate remains `management_plan_only_execution_gate_missing` even if the corresponding boolean flag is `true`. This prevents the automation from repeatedly restating blockers while also preventing vague gate claims from authorizing order preparation.

## New-Market Learning Rule

When a portfolio-manager trade in a new or uncovered market succeeds, the follow-up is mandatory:

- create or update a GitHub issue for a replay/backtest/domain-lane test if the setup appears repeatable
- update Obsidian with the trade thesis, why it worked, what not to overgeneralize, and what future test would validate it
- record the realized/unrealized return contribution against the `1,000`, `10,000`, and `100,000` proof thresholds when available
- record whether the insight belongs in a future domain lane, a profile-study lesson, or a one-off operator/manual case

A single winning trade is evidence for a test, not authority to scale the domain.

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
- `management_plan_only_execution_gate_missing`
- `no_material_change`

## Must Not Do

- Do not bypass Janus order validators, direct CLOB/account truth, or kill switches.
- Do not use `codex_tool/*` Janus API wrappers as if they were independent Polymarket execution tools.
- Do not use `tools/polymarket_smoke_order.py` from automation or portfolio-manager passes.
- Do not use this automation to validate Janus NBA/WNBA live trades.
- Do not merge global-portfolio risk with NBA/WNBA live-testing budgets.
- Do not promote an uncovered category directly to autonomous scaled trading.
- Do not use market orders unless a separate exception policy allows it for that exact case.
- Do not create duplicate Obsidian notes for every pass; edit, merge, split, and relink first.
