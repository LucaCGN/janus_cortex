# Janus Premise To Backlog Map - 2026-05-18

Status: post-foundation backlog map
Created: 2026-05-18

## Purpose

Translate the accepted premise register, operator corrections, repo docs, runtime reports, and Obsidian bootstrap into an issue-backed backlog after the initial seed issues `#17-#29` closed.

This is not a replacement for GitHub. GitHub issues remain the durable task identity. This file explains why those issues exist and where future work belongs.

## Research Basis

Primary repo sources:

- `app/docs/planning/current/janus_final_system_premise_register.md`
- `app/docs/planning/current/final_system/premise_decisions_2026-05-17.md`
- `app/docs/planning/current/final_system/source_of_truth_map.md`
- `app/docs/planning/current/final_system/market_scope_registry.md`
- `app/docs/planning/current/final_system/global_ego_and_purpose.md`
- `app/docs/planning/current/final_system/automation/*`
- `app/docs/planning/current/final_system/backlog/immediate_issue_seed_2026-05-17.md`
- `local/shared/reports/daily-live-validation/*`

Obsidian sources:

- `00_Janus_Control/Janus Master Index.md`
- `00_Janus_Control/Issue Backlog Index.md`
- `20_Trading_Knowledge/*`
- `40_Profile_Studies/*`

## Backlog Layers

| Layer | Meaning | Current Use |
|---|---|---|
| P0 activation blockers | Must be resolved before trusting recurring controller/live-readiness flow or active order-path automations. | `#61` for next NBA min-size live trade routing and `#62` for WNBA min-size live-promotion; older portfolio policy/tool split and real-call proof foundations `#54/#56/#59` are closed. |
| P1 readiness builders | Needed for NBA/WNBA minimum-size testing, reliable reviews, and future portfolio-manager action-readiness follow-ups. | `#42`, `#44`, `#55`; `#50`, `#56`, and `#60` are closed foundations. |
| P2 research/incubation | Future-domain and profile-study work that has not yet been promoted into required portfolio-manager actions. | `#46`, `#47`, `#48` |
| Closed foundations | Implemented bootstrap surfaces that need validation, not reopening. | `#17-#30`, `#31`, `#32`, `#33`, `#34`, `#35`, `#36`, `#37`, `#39`, `#40`, `#41`, `#49` |
| Governance hygiene | Ongoing Obsidian/repo drift checks and future project setup if needed. | Closed foundation `#30`; open a focused follow-up only when new hygiene work is material. |

## Current Follow-Up Issue Backlog

| Issue | Priority | Backlog Layer | Premise Coverage | Purpose |
|---|---|---|---|---|
| [#30](https://github.com/LucaCGN/janus_cortex/issues/30) | P0 | Closed foundation | P115-P122 | GitHub taxonomy labels created and closed seed issues `#17-#29` retro-labeled. |
| [#32](https://github.com/LucaCGN/janus_cortex/issues/32) | P0 | Closed foundation | P091-P109 | Repo-local runtime root and controller activation gate validated on 2026-05-18. |
| [#37](https://github.com/LucaCGN/janus_cortex/issues/37) | P0 | Closed foundation | P019-P030, P146-P149, P246-P255 | Fresh-DB NBA probe/account mapping gaps repaired and HTTP-path validated. |
| [#38](https://github.com/LucaCGN/janus_cortex/issues/38) | P0 | Closed foundation | P004-P018, NP001-NP010 | Encode global ego/purpose across repo prompts and Obsidian. |
| [#39](https://github.com/LucaCGN/janus_cortex/issues/39) | P0 | Closed foundation | P078-P090, P119-P122 | Active queue locks and controller pass ledger implemented for repo-local controller passes. |
| [#40](https://github.com/LucaCGN/janus_cortex/issues/40) | P0 | Closed foundation | P036-P069, P289, P294 | Current architecture and service degradation maps completed. |
| [#41](https://github.com/LucaCGN/janus_cortex/issues/41) | P0 | Closed foundation | P212-P228, NP003-NP006 | Budget-aware model routing and Codex fallback StrategyPlanJSON adoption/evaluation path validated. |
| [#33](https://github.com/LucaCGN/janus_cortex/issues/33) | P1 | Closed foundation | Seed issues `#17-#29` | API-up validation completed against the running non-live API. |
| [#34](https://github.com/LucaCGN/janus_cortex/issues/34) | P1 | Closed foundation | P257-P265 | WNBA minimal-readiness dry run completed; follow-up `#50` owns passive capture and season-level shadow backtests. |
| [#42](https://github.com/LucaCGN/janus_cortex/issues/42) | P1 | Readiness builder | P138-P142, P197-P199 | Validate minimum order constraints and market-order exception policy. |
| [#43](https://github.com/LucaCGN/janus_cortex/issues/43) | P1 | Closed foundation | P243-P251 | Analytical chart-equivalent metrics implemented for event review/live monitor. |
| [#44](https://github.com/LucaCGN/janus_cortex/issues/44) | P1 | Readiness builder | P190-P208, P252-P255 | Calibrate profit-ratcheted risk ladder from account and DB histories. |
| [#45](https://github.com/LucaCGN/janus_cortex/issues/45) | P1 | Closed foundation | P268-P274 | Global portfolio target/rebuy ledger and watchlist schema implemented. |
| [#52](https://github.com/LucaCGN/janus_cortex/issues/52) | P0 | Closed foundation | P268-P274, operator correction 2026-05-18 | Active Codex global portfolio-manager policy, prompt, ledger, and preview surfaces implemented. |
| [#53](https://github.com/LucaCGN/janus_cortex/issues/53) | P0 | Closed foundation | P268-P274, NP005-NP006, operator correction 2026-05-18 | Codex tooling split and preview-first Polymarket fallback base implemented. |
| [#54](https://github.com/LucaCGN/janus_cortex/issues/54) | P0 | Closed foundation | P268-P274, operator correction 2026-05-18 | Approved global portfolio execution gate proof, concrete Janus order-management adapter, runtime activation guard, risk/rate evidence, ledger finalization, confirmation-id handling, and idempotency replay hardening implemented. |
| [#59](https://github.com/LucaCGN/janus_cortex/issues/59) | P0 | Closed portfolio/order-path activation proof | P268-P274, operator correction 2026-05-18 | Portfolio-manager real-call reconciliation proof completed; future portfolio drift or expansion needs a new focused issue. |
| [#61](https://github.com/LucaCGN/janus_cortex/issues/61) | P0 | Active NBA live-readiness/trade route | P138-P142, P190-P208, P212-P228, P243-P255, operator correction 2026-05-20 | Execute the next NBA playoff minimum-size live trade through Janus StrategyPlan/evaluate/execute/live-worker gates or record the exact blocker before the live window ends. |
| [#62](https://github.com/LucaCGN/janus_cortex/issues/62) | P0 | Active WNBA live-promotion route | P257-P265, P138-P142, P190-P208, operator correction 2026-05-20 | Promote WNBA from passive/shadow capture to controlled minimum-size live test readiness with WNBA StrategyPlan, direct CLOB, worker/runtime, and gate evidence. |
| [#56](https://github.com/LucaCGN/janus_cortex/issues/56) | P1 | Closed portfolio/scanner readiness builder | P268-P274, operator correction 2026-05-19 | Required portfolio-manager action planning, frontend/profile discovery enforcement, one-shot portfolio order routing, approved 1c grid-service spawn proof, cross-league basketball scanner, and 20-slot governance completed. |
| [#49](https://github.com/LucaCGN/janus_cortex/issues/49) | P1 | Closed foundation | P268-P274, global portfolio evidence | Direct open CLOB order mirror endpoint implemented and runtime-validated. |
| [#50](https://github.com/LucaCGN/janus_cortex/issues/50) | P1 | Closed foundation | P257-P265 | WNBA passive/shadow baseline and blocker report published; sustained active-window capture/audit moved through closed `#60`. |
| [#55](https://github.com/LucaCGN/janus_cortex/issues/55) | P1 | Research/readiness support | P243-P255, operator correction 2026-05-19 | Compare NBA pregame, immediate-live, post-Q1, and post-Q1-stability entry timing with fillability and event-start cancellation effects; supports `#61` but does not own live execution. |
| [#60](https://github.com/LucaCGN/janus_cortex/issues/60) | P1 | Closed foundation | P257-P265, operator correction 2026-05-19 | Sustained WNBA active-window passive CLOB/trade capture and audit integration completed; WNBA live promotion moved to `#62`. |
| [#46](https://github.com/LucaCGN/janus_cortex/issues/46) | P2 | Research/incubation | P270-P274 | Turn winning profile studies into benchmark hypotheses. |
| [#47](https://github.com/LucaCGN/janus_cortex/issues/47) | P2 | Research/incubation | P127-P134, P266 | Incubate crypto up/down options research and backtest lane. |
| [#48](https://github.com/LucaCGN/janus_cortex/issues/48) | P2 | Research/incubation | P267-P274 | Incubate geopolitics, economics, and culture monitoring lanes. |

## Deferred Ideas Not Yet Issue-Ready

| Idea | Why Deferred | Promotion Trigger |
|---|---|---|
| Direct raw Codex/MCP Polymarket manager bypass | Raw connector execution must not bypass direct CLOB truth, risk, order, ledger, idempotency, reconciliation, and kill-switch gates. | `#54` implemented the first concrete approved execution gate proof after `#52/#53` base policy/tooling acceptance; closed `#59` proved real-call activation and reconciliation through the approved Janus path. |
| Multiple FastAPI apps or Redis-backed workers | Current direction is modular monolith first. | `#40` proves modular monolith cannot meet independence/latency needs. |
| Fully automated future-domain execution | Basketball and core ledger/risk/review are not stable enough. | `#47` or `#48` reach shadow evidence and min-size-test criteria. |
| Frontier model as normal live analyst | Cost/return ratio is not justified at current bankroll scale. | `#41` defines budget state and realized returns justify escalation. |

## Execution Order

1. Completed: close or unblock `#37`.
2. Completed: validate controller activation gate through `#32`.
3. Completed: produce architecture/degradation maps in `#40`.
4. Completed: validate budget-aware LLM/Codex fallback in `#41`.
5. Completed: run API-up validation `#33`.
6. Completed: run WNBA dry run `#34`.
7. Completed: validate direct open CLOB order mirroring through `#49`.
8. Completed: close `#52` after active Codex global portfolio-manager authority, gates, trend-lane evidence requirements, action ledger, and order-management preview were implemented.
9. Completed: close `#53` after base `codex_tools/janus` and `codex_tools/polymarket` split, preview-first fallback gates, ledger, account reads, CLI, and compatibility wrappers were implemented.
10. Tomorrow's covered-market readiness is `#61` first for NBA and `#62` first for WNBA. These outrank global portfolio, crypto/options, and stale closed WNBA work until each has a fresh readiness checkpoint or exact blocker.
11. Portfolio-manager readiness foundations `#56/#59` are closed. Future portfolio-manager drift, scaling, or grid-service expansion should be opened as focused follow-up issues and should not preempt NBA/WNBA readiness unless direct live-money safety is unclear.
12. Use `#42` and `#44` as support issues for minimum order constraints and profit-ratcheted risk ladder when the live readiness route needs those policies.
13. Use `#46-#48` for future-domain and profile research without live authority unless promoted by the closed `#54/#59` execution-gate foundations and current operator-approved gates.

## Issue Creation Rule Going Forward

New issues should be created only when they have:

- source premise or evidence link
- owner persona
- market/domain scope
- acceptance criteria
- live-order impact
- validation plan
- must-not-do constraints

The controller should avoid creating issues for raw brainstorms unless the idea has moved from idea backlog to planned backlog.
