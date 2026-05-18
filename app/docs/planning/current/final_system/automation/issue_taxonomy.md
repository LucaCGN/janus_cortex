# Janus GitHub Issue Taxonomy

Status: draft control contract
Created: 2026-05-17

## Purpose

Define the issue labels and issue levels the controller and issue-backlog-manager should use.

GitHub issues are the durable backlog. They are not runtime truth and do not override repo contracts or Janus/CLOB state.

## Type Labels

If GitHub native issue types are available, map them to the closest `type:*` label and keep the label for automation-friendly filtering.

| Label | Meaning |
|---|---|
| `type:bug` | Incorrect behavior, regression, broken assumption. |
| `type:feature` | New product/runtime capability. |
| `type:design` | Spec, architecture, controller, or policy design. |
| `type:docs` | Repo docs or Obsidian structure. |
| `type:ops` | Runtime, automation, handoff, deployment, service readiness. |
| `type:research` | External profile/domain/signal research. |
| `type:test` | Test, replay, validation, benchmark, fillability work. |
| `type:incident` | Cost, live-money, CLOB, runtime, or safety incident. |

## Priority Labels

| Label | Meaning |
|---|---|
| `priority:P0` | Blocks safe automation, live readiness, source-of-truth integrity, or order safety. |
| `priority:P1` | Needed for next readiness milestone or high-priority domain promotion. |
| `priority:P2` | Important hardening, calibration, replay, reporting, or quality improvement. |
| `priority:P3` | Future domain, idea, nonblocking improvement. |

## Market Labels

| Label | Scope |
|---|---|
| `market:sports` | Sports domain generally. |
| `market:basketball` | Shared basketball logic. |
| `market:nba` | NBA-specific calibration/runtime. |
| `market:wnba` | WNBA-specific calibration/runtime. |
| `market:global-portfolio` | Positions and targets outside Janus-controlled events. |
| `market:crypto` | Crypto options and related markets. |
| `market:geopolitics` | Geopolitical markets. |
| `market:economics` | Macro/economic markets. |
| `market:culture` | Culture/long-tail markets. |

## Lane Labels

| Label | Purpose |
|---|---|
| `lane:controller` | Master controller and automation flow. |
| `lane:docs-memory` | Repo docs, Obsidian, source-of-truth hygiene. |
| `lane:github-devops` | Labels, issues, projects, branch/worktree governance. |
| `lane:runtime-cost` | LLM cost, budgets, dedup, shutdown. |
| `lane:event-review` | Event bundle, postgame, missed windows, timelines. |
| `lane:ledger` | Fills, orders, lifecycle, attribution. |
| `lane:execution` | Order-manager, validators, manual assistant. |
| `lane:llm-orchestration` | Routing, prompts, fallback, adoption. |
| `lane:basketball-intelligence` | Scenario, regime, quarter, PBP, microstructure. |
| `lane:strategy-sleeves` | Strategy modules, dependency graph, hedges, OT. |
| `lane:risk-manager` | Profit-ratcheted risk, tail risk, exposure caps. |
| `lane:wnba-readiness` | WNBA passive, replay, minimal testing readiness. |
| `lane:portfolio-manager` | Global portfolio analysis and target/rebuy logic. |
| `lane:future-domain` | Crypto, geopolitics, economics, culture incubation. |

## Lifecycle Labels

| Label | Meaning |
|---|---|
| `phase:pregame` | Before sports event start. |
| `phase:live` | Active event/window. |
| `phase:postgame` | Review after event close. |
| `phase:settlement` | Market/account settlement and reconciliation. |
| `phase:monitor` | Long-term portfolio/watch monitoring. |
| `phase:research` | Domain or signal research. |
| `phase:shadow` | Passive capture or dry-run mode. |
| `phase:backtest` | Replay, simulation, fillability, benchmark work. |

## Maturity Labels

| Label | Meaning |
|---|---|
| `stage:idea` | Concept only. |
| `stage:research` | Research/spec required. |
| `stage:shadow` | Passive or dry-run evidence required. |
| `stage:min-size-test` | Minimum live test candidate. |
| `stage:live-limited` | Bounded live operation. |
| `stage:active` | Normal risk-managed operation. |
| `stage:scaled` | Progressive capital increase after evidence. |

## Impact Labels

| Label | Meaning |
|---|---|
| `live-impact:none` | No runtime/live trading impact. |
| `live-impact:read-only` | Reads runtime/account state only. |
| `live-impact:shadow` | Dry-run, passive capture, replay, no orders. |
| `live-impact:live-readiness` | Affects readiness gates. |
| `live-impact:order-path` | Touches validators/order execution/manual assistant. |
| `runtime-impact:restart` | May require service restart. |
| `runtime-impact:migration` | May require data/schema migration. |

## Issue Body Minimum

Every sprint-ready issue should include:

- problem statement
- market/domain scope
- lane
- persona owner
- acceptance criteria
- affected files/modules if known
- live-order impact
- runtime impact
- validation/tests
- evidence links
- Obsidian links if useful

## Current Setup Status

Native GitHub taxonomy labels were created on 2026-05-18 and open follow-up issues `#30-#36` were labeled.

Open issue [#30](https://github.com/LucaCGN/janus_cortex/issues/30) remains the project-hygiene tracker for retroactive labeling of closed seed issues `#17-#29` and any future GitHub Projects/type setup.

Issue titles and bodies should still include the same taxonomy fields explicitly because labels are not runtime truth and can be lost during exports:

- Priority
- Type
- Market
- Lane
- Phase or stage when relevant
- Live impact
