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
| P0 activation blockers | Must be resolved before trusting recurring controller/live-readiness flow. | `#32`, `#37`, `#40`, `#41` |
| P1 readiness builders | Needed for WNBA/NBA minimum-size testing and reliable reviews. | `#33`, `#34`, `#42`, `#43`, `#44`, `#45` |
| P2 research/incubation | Future-domain, profile-study, and strategy-expansion work. | `#46`, `#47`, `#48` |
| Closed foundations | Implemented bootstrap surfaces that need validation, not reopening. | `#17-#29`, `#31`, `#35`, `#36`, `#39` |
| Governance hygiene | Issue labels, project hygiene, Obsidian/repo drift checks. | `#30` |

## Current Open Issue Backlog

| Issue | Priority | Backlog Layer | Premise Coverage | Purpose |
|---|---|---|---|---|
| [#30](https://github.com/LucaCGN/janus_cortex/issues/30) | P0 | Governance hygiene | P115-P122 | Complete GitHub project/label hygiene and optional seed retro-labeling. |
| [#32](https://github.com/LucaCGN/janus_cortex/issues/32) | P0 | Activation blocker | P091-P109 | Validate repo-local runtime root and controller activation gate. |
| [#37](https://github.com/LucaCGN/janus_cortex/issues/37) | P0 | Activation blocker | P019-P030, P146-P149, P246-P255 | Resolve fresh-DB NBA probe/account mapping gaps. |
| [#38](https://github.com/LucaCGN/janus_cortex/issues/38) | P0 | Closed foundation | P004-P018, NP001-NP010 | Encode global ego/purpose across repo prompts and Obsidian. |
| [#39](https://github.com/LucaCGN/janus_cortex/issues/39) | P0 | Closed foundation | P078-P090, P119-P122 | Active queue locks and controller pass ledger implemented for repo-local controller passes. |
| [#40](https://github.com/LucaCGN/janus_cortex/issues/40) | P0 | Activation blocker | P036-P069, P289, P294 | Build current architecture and service degradation maps. |
| [#41](https://github.com/LucaCGN/janus_cortex/issues/41) | P0 | Activation blocker | P212-P228, NP003-NP006 | Validate budget-aware model routing and Codex fallback state. |
| [#33](https://github.com/LucaCGN/janus_cortex/issues/33) | P1 | Readiness builder | Seed issues `#17-#29` | Validate closed seed foundations against a running API. |
| [#34](https://github.com/LucaCGN/janus_cortex/issues/34) | P1 | Readiness builder | P257-P265 | Run WNBA minimal-readiness dry run without live orders. |
| [#42](https://github.com/LucaCGN/janus_cortex/issues/42) | P1 | Readiness builder | P138-P142, P197-P199 | Validate minimum order constraints and market-order exception policy. |
| [#43](https://github.com/LucaCGN/janus_cortex/issues/43) | P1 | Readiness builder | P243-P251 | Add analytical chart-equivalent metrics to event review bundle. |
| [#44](https://github.com/LucaCGN/janus_cortex/issues/44) | P1 | Readiness builder | P190-P208, P252-P255 | Calibrate profit-ratcheted risk ladder from account and DB histories. |
| [#45](https://github.com/LucaCGN/janus_cortex/issues/45) | P1 | Readiness builder | P268-P274 | Build global portfolio target/rebuy ledger and watchlist schema. |
| [#46](https://github.com/LucaCGN/janus_cortex/issues/46) | P2 | Research/incubation | P270-P274 | Turn winning profile studies into benchmark hypotheses. |
| [#47](https://github.com/LucaCGN/janus_cortex/issues/47) | P2 | Research/incubation | P127-P134, P266 | Incubate crypto up/down options research and backtest lane. |
| [#48](https://github.com/LucaCGN/janus_cortex/issues/48) | P2 | Research/incubation | P267-P274 | Incubate geopolitics, economics, and culture monitoring lanes. |

## Deferred Ideas Not Yet Issue-Ready

| Idea | Why Deferred | Promotion Trigger |
|---|---|---|
| Direct Codex/MCP Polymarket manager | Requires explicit permission model and ngrok/MCP design. | `#45` defines watchlist schema and a separate execution policy is approved. |
| Multiple FastAPI apps or Redis-backed workers | Current direction is modular monolith first. | `#40` proves modular monolith cannot meet independence/latency needs. |
| Fully automated future-domain execution | Basketball and core ledger/risk/review are not stable enough. | `#47` or `#48` reach shadow evidence and min-size-test criteria. |
| Frontier model as normal live analyst | Cost/return ratio is not justified at current bankroll scale. | `#41` defines budget state and realized returns justify escalation. |

## Execution Order

1. Close or unblock `#37`.
2. Validate controller activation gate through `#32`.
3. Produce architecture/degradation maps in `#40`.
4. Validate budget-aware LLM/Codex fallback in `#41`.
5. Run API-up validation `#33`.
6. Run WNBA dry run `#34`.
7. Expand review/risk/execution metrics through `#42-#45`.
8. Use `#46-#48` for future-domain and profile research without live authority.

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
