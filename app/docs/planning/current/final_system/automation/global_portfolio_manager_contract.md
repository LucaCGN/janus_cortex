# Janus Global Portfolio Manager Contract

Status: active intent contract
Created: 2026-05-18
GitHub issue: https://github.com/LucaCGN/janus_cortex/issues/52
Current automation id: `janus-portfolio-manager`
Prompt file: `app/docs/planning/current/final_system/automation/global_portfolio_manager_prompt.md`

## Purpose

Define the separate portfolio-management automation for non-Janus or partially-Janus Polymarket exposure.

This automation is not a validator for NBA/WNBA Janus trades and is not merely a read-only explorer. Its intended job is to help Janus make money across the operator's broader expectation-markets portfolio by:

- managing already-existing positions that the operator chose to buy
- maintaining or closing matching sell targets when direct CLOB truth and Janus gates allow it
- finding attractive trend-following opportunities in uncovered market categories
- turning successful new-market trades into backlog tests, domain-lane candidates, and Obsidian lessons

It inherits `app/docs/planning/current/final_system/global_ego_and_purpose.md`: Janus should trade trends, liquidity, market structure, and return paths, not pretend it can predict final outcomes directly.

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

Until the prior LLM token-spend bug is proven contained with durable runtime evidence, the `janus-portfolio-manager` automation must run on a mini/nano budget posture, not frontier reasoning.

- Default Codex app model: `gpt-5.4-mini`.
- Default reasoning effort: `low`.
- Nano-class routing should be preferred inside app-owned LLM calls when that surface exists and the task is summarization, classification, checklisting, or watchlist maintenance.
- Frontier models, including `gpt-5.5`, are blocked for this automation unless a separate issue-backed cost/readiness review proves budget controls, caps, telemetry, and shutdown behavior are safe.
- If a requested portfolio action appears to require frontier reasoning, the automation must stop at a written management plan and route the escalation as a GitHub issue instead of spending frontier tokens.

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

For markets where Janus has no current position, the portfolio manager may scout uncovered categories such as geopolitics, economics, culture, crypto, sports futures, and other prediction-market domains.

The premise is trend trading, not final-outcome prediction. A candidate must record:

- category and market
- catalyst or trend path
- current price path and microstructure
- liquidity, spread, depth, and minimum-order feasibility
- expected return path and target/stop structure
- maximum risk and portfolio budget bucket
- why this is a trend or mispricing setup rather than a raw outcome prediction
- what would falsify the trade

New-market trend entries require stronger gates than existing-position target maintenance because they expand the portfolio into uncovered categories.

## Execution Authority Gate

The portfolio manager is intended to become trading-capable, but it may only place, cancel, replace, submit, or prepare orders when all required authority gates are true:

1. Direct CLOB/account truth is fresh and resolves the relevant market, token, open orders, fills, collateral, and position state.
2. Janus API/order manager exposes an explicit global-portfolio execution path for the action.
3. The action is recorded in a portfolio ledger with source evidence, strategy reason, target/stop/rebuy policy, and external order ids when available.
4. A global-portfolio risk budget exists separately from NBA/WNBA live-testing risk.
5. The action satisfies Polymarket minimum-size/minimum-notional constraints and any market-order exception policy.
6. A kill switch or disabled execution flag is not active.
7. The automation can prove that it is not using screenshots, stale portfolio mirrors, or chat memory as execution truth.

If any gate is missing, the pass must fall back to management planning: update the watchlist, write the blocker, and create or update the relevant GitHub issue.

## New-Market Learning Rule

When a portfolio-manager trade in a new or uncovered market succeeds, the follow-up is mandatory:

- create or update a GitHub issue for a replay/backtest/domain-lane test if the setup appears repeatable
- update Obsidian with the trade thesis, why it worked, what not to overgeneralize, and what future test would validate it
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
- Do not use this automation to validate Janus NBA/WNBA live trades.
- Do not merge global-portfolio risk with NBA/WNBA live-testing budgets.
- Do not promote an uncovered category directly to autonomous scaled trading.
- Do not use market orders unless a separate exception policy allows it for that exact case.
- Do not create duplicate Obsidian notes for every pass; edit, merge, split, and relink first.
