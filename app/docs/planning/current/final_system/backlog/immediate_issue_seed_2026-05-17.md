# Immediate Issue Seed - 2026-05-17

Status: implemented bootstrap seed
Repo target: `LucaCGN/janus_cortex`

## Purpose

Convert the immediate P0/P1 backlog into durable GitHub issues for the first controller/agent loop.

These are not the full final-system specs. They were the initial issue set used to stabilize current Janus, bootstrap the source-of-truth framework, and prepare WNBA/NBA live testing.

Implementation note 2026-05-17: issues `#17-#29` now have tested foundations on `main`. Future work should open narrower follow-up issues for deeper calibration, live-readiness promotion, and production hardening instead of reopening this seed.

Follow-up note 2026-05-18: current open follow-up issues are `#30-#36`. These cover GitHub label taxonomy, post-reconciliation handoff refresh, repo-local runtime gate validation, API-up validation, WNBA dry run, global portfolio explorer automation, and stale ML branch cleanup.

## Issue Labels Proposed

- `priority:P0`
- `priority:P1`
- `lane:runtime-cost`
- `lane:event-review`
- `lane:ledger`
- `lane:llm`
- `lane:risk`
- `lane:basketball-intelligence`
- `lane:wnba`
- `lane:docs-ops`
- `type:bug`
- `type:feature`
- `type:design`

## P0 Issues

### JANUS-P0-001 - Add LLM Runtime Cost Budgets, Dedup, And Final Shutdown

Lane: `runtime-cost-shutdown`
Type: bug/feature
Reason: May 13 cost incident repeated stale/final triggers and made live LLM dispatch unsafe.

Acceptance criteria:

- Repeated trigger hash dedup prevents same trigger from dispatching unless new PBP/CLOB/portfolio evidence changes.
- Event-final flat shutdown disables worker dispatch when game final, market near settled, and event-scoped direct CLOB state is flat.
- Per-event LLM token/cost budget exists with soft warning, model downgrade, and hard stop.
- Model-call caps exist by trigger type.
- Integrity gate blocks live LLM dispatch if controls are missing.
- Tests cover repeated trigger, final/flat shutdown, and budget exceeded paths.

### JANUS-P0-002 - Build Event Review Bundle Endpoint And Decision Timeline

Lane: `event-review-reportability`
Type: feature
Reason: Postgame reconstruction required screenshots, grep, manual LLM artifact parsing, and stale mirror judgment.

Acceptance criteria:

- One API/tool call returns event PnL, fills, orders, positions, plan versions, LLM traces, deterministic decisions, ML evidence, PBP summary, CLOB windows, blockers, missed windows, and cost telemetry.
- Output separates autonomous Janus, Codex-assisted Janus, user/manual, and external/out-of-scope activity.
- Output includes analytical chart equivalents: inversions, oscillation amplitude, spike count, trend smoothness, grid opportunity count.
- Postgame agent can use bundle without screenshots.

### JANUS-P0-003 - Repair Account-Scoped Fill Ledger And Lifecycle Attribution

Lane: `execution-ledger`
Type: bug/feature
Reason: DB-origin Janus performance is incomplete and currently negative because fills are not linked reliably to strategy/order lifecycle.

Acceptance criteria:

- Direct CLOB order ids and trade ids link to local order ids.
- Duplicate account Data API fills are deduplicated.
- Entry, target, stop, hedge, manual adoption, and settlement are lifecycle-grouped.
- Orders/fills store strategy plan version, strategy id, intent id, origin actor, and parent order id.
- Stale submitted rows are unresolved until reconciled.
- Event-level settlement valuation is available.

### JANUS-P0-004 - Implement Current-Event Inventory In Every Review And Revision

Lane: `llm-orchestration`
Type: bug
Reason: Manual orders exposed that not all LLM/review contexts included complete current-event orders, positions, targets, and direct trades.

Acceptance criteria:

- Every LLM revision request includes all direct current-event open orders, positions, known targets, manual orders, direct trades, and stale mirror rows.
- Strategy review explicitly classifies each inventory item as Janus, Codex-assisted, user/manual, unknown, stale, or out-of-scope.
- Missing inventory state blocks new exposure.

### JANUS-P0-005 - Add Safe LLM/Codex Strategy Adoption And Fallback Flow

Lane: `llm-orchestration`
Type: feature/design
Reason: Internal LLM may be unavailable or cost-blocked, and Codex must be able to replace strategy-crafting without bypassing Janus safety.

Acceptance criteria:

- API/DB/runtime artifacts expose `internal_llm_unavailable` and `codex_strategy_required` state.
- Codex can submit StrategyPlanJSON fallback candidates from current event context.
- Conservative actions can be auto-adopted when policy allows.
- New exposure and tail-risk actions require human review until proven.
- Janus validators still gate execution.

### JANUS-P0-006 - Build Direct CLOB Manual Order Assistant

Lane: `execution-ledger`
Type: feature
Reason: Polymarket UI delay/misclick caused excessive tail-risk loss in WNBA final-possession scenario.

Acceptance criteria:

- User/Codex can request preview with outcome, side, max price, max notional, event id, and intent.
- Tool fetches direct CLOB and account state immediately.
- Tool rejects stale book, price above cap, notional above cap, event mismatch, side mismatch, or missing current-event reconciliation.
- Tool submits only through audited Janus path when confirmed.
- Tail-risk orders have stricter freshness and notional caps.

### JANUS-P0-007 - Bootstrap Repo/Obsidian/GitHub/Queue Source-Of-Truth Framework

Lane: `docs-obsidian-github`
Type: design/ops
Reason: Final automation must use mutable repo docs and Obsidian, not chat memory.

Acceptance criteria:

- Final-system repo docs exist and link to Obsidian bootstrap notes.
- Initial GitHub issues are created from this seed.
- Task queue schema exists.
- Controller automation reads the repo contract.
- Obsidian index and first notes are populated.
- A docs/memory health check procedure exists.

## P1 Issues

### JANUS-P1-001 - Build Basketball Regime And Scenario Classifier

Lane: `basketball-intelligence`
Type: feature
Reason: S/A/B/C/D taxonomy must become app-owned detection, not manual interpretation.

Acceptance criteria:

- Classifies stable close oscillation, expectation inversion, slow underdog descent, favorite floor, blowout/falling knife, clutch, garbage, OT, timeout/dead-ball, star shock.
- Outputs confidence, evidence, and allowed sleeves.
- Provides analytical equivalents to chart reading.

### JANUS-P1-002 - Build Quarter And PBP Price-Impact Feature Lane

Lane: `basketball-intelligence`
Type: feature
Reason: Fast repeatable game-state interpretation should move from expensive LLM reasoning into deterministic/ML lanes.

Acceptance criteria:

- PBP rows tagged with event type, player role, quarter, clock, score delta, run state, foul pressure, substitutions, timeout, and star/bench role.
- Before/after CLOB windows label price response.
- Quarter transition triggers exist for Q1, Q2 bench, halftime, Q3 adjustment, Q4 clutch/garbage, OT.
- NBA and WNBA share schema with separate calibration.

### JANUS-P1-003 - Build Strategy Sleeve Generation And Dependency Graph

Lane: `strategy-sleeves`
Type: feature
Reason: CLE/DET needed DET Q4 and OT sleeves before Codex/operator intervention.

Acceptance criteria:

- App can generate bounded candidate sleeves when regime changes.
- Sleeve categories include underdog rebound, favorite floor, micro-grid, clutch hedge, OT rebound, tail optionality, winner definition.
- Sleeves coordinate budgets and inventory through a dependency graph.
- If live authority is missing, candidates are logged as shadow/missed windows.

### JANUS-P1-004 - Build Profit-Ratcheted Risk Manager

Lane: `risk-manager`
Type: feature
Reason: Base bankroll and realized-profit risk must be separated before scaling.

Acceptance criteria:

- Tracks base exposure, event/day realized PnL, open unrealized PnL, low/medium/high sleeve budgets, unresolved inventory, and tail-risk budget.
- Exposure grows from realized profit, not from open unrealized gains.
- Tail risk is funded only from realized profit with hard caps.
- LLM/risk prompt profile changes by realized-return state.

### JANUS-P1-005 - Replace Close-Game Stop-First Policy With Virtual-Dead Policy

Lane: `risk-manager`
Type: feature
Reason: CLE/DET showed premature loss realization can destroy still-live optionality.

Acceptance criteria:

- Loss exit requires virtual-dead, final, garbage, severe late deficit, bench-emptying, or unsafe truth evidence.
- Engine compares hold, target reduction, hedge, add-down, and close before realizing losses.
- Hedge validation accounts for spread and expected loss reduction.

### JANUS-P1-006 - Promote WNBA To Minimal Live-Readiness Track

Lane: `wnba-readiness`
Type: feature
Reason: NBA playoff test windows are sparse and WNBA provides critical live-test surface.

Acceptance criteria:

- WNBA uses shared basketball event/PBP/orderbook/replay/report contracts.
- WNBA liquidity and spread calibration report exists.
- WNBA passive CLOB capture and closed-market price history feed replay.
- Minimal 5-share or true-exchange-minimum test readiness criteria are documented.
- No WNBA live order runs until core safety gates and operator approval.

## Issue Creation Rule

When creating GitHub issues, include:

- Title.
- Lane.
- Priority.
- Problem statement.
- Acceptance criteria.
- Evidence links.
- Must-not-do constraints.
- Initial suggested files/modules.

## Created GitHub Issues

Created on 2026-05-17 in `LucaCGN/janus_cortex`.

| Seed ID | GitHub Issue |
|---|---|
| `JANUS-P0-001` | https://github.com/LucaCGN/janus_cortex/issues/17 |
| `JANUS-P0-002` | https://github.com/LucaCGN/janus_cortex/issues/18 |
| `JANUS-P0-003` | https://github.com/LucaCGN/janus_cortex/issues/19 |
| `JANUS-P0-004` | https://github.com/LucaCGN/janus_cortex/issues/20 |
| `JANUS-P0-005` | https://github.com/LucaCGN/janus_cortex/issues/21 |
| `JANUS-P0-006` | https://github.com/LucaCGN/janus_cortex/issues/22 |
| `JANUS-P0-007` | https://github.com/LucaCGN/janus_cortex/issues/23 |
| `JANUS-P1-001` | https://github.com/LucaCGN/janus_cortex/issues/24 |
| `JANUS-P1-002` | https://github.com/LucaCGN/janus_cortex/issues/25 |
| `JANUS-P1-003` | https://github.com/LucaCGN/janus_cortex/issues/26 |
| `JANUS-P1-004` | https://github.com/LucaCGN/janus_cortex/issues/27 |
| `JANUS-P1-005` | https://github.com/LucaCGN/janus_cortex/issues/28 |
| `JANUS-P1-006` | https://github.com/LucaCGN/janus_cortex/issues/29 |

## Bootstrap Implementation Status

| Issue | Status | Implementation Surface |
|---|---|---|
| `#17` | Closed | `app/modules/agentic/llm_runtime.py` safety controls, budget/dedup/final shutdown tests. |
| `#18` | Closed | `/v1/events/{event_id}/review-bundle`, decision timeline, token/cost timeline, actor attribution, microstructure and missed-opportunity candidates. |
| `#19` | Closed | Portfolio lifecycle reconciliation, direct CLOB trade dedupe, actor attribution, unresolved lifecycle reporting. |
| `#20` | Closed | Current-event inventory proof in LLM/live tick context and manual assistant inventory snapshots. |
| `#21` | Closed | LLM runtime Codex-required state, StrategyPlanJSON adoption, conservative action adoption artifact/proof. |
| `#22` | Closed | `/v1/events/{event_id}/manual-order-assistant`, preview/execute gate, max price/notional/book/inventory validation. |
| `#23` | Closed | Repo docs, Obsidian bootstrap, issue seed, controller/queue docs. |
| `#24` | Closed | `app/modules/agentic/basketball_logic.py::classify_basketball_regime`. |
| `#25` | Closed | PBP tagger and before/after price-impact window helpers. |
| `#26` | Closed | Strategy sleeve candidate generation and dependency graph helpers. |
| `#27` | Closed | Profit-ratcheted risk-state helper with base/profit/sleeve/tail budgets. |
| `#28` | Closed | Virtual-dead classifier and loss-exit comparison requirements. |
| `#29` | Closed | WNBA minimal live-readiness gate with shared basketball contract and calibration blockers. |

## Current Follow-Up Issues

Created on 2026-05-18 after the seed foundation closed.

| Issue | Priority | Purpose |
|---|---|---|
| [#30](https://github.com/LucaCGN/janus_cortex/issues/30) | P0 | Create GitHub issue taxonomy labels and project hygiene. |
| [#31](https://github.com/LucaCGN/janus_cortex/issues/31) | P0 | Refresh runtime handoffs after 2026-05-18 event reconciliation. |
| [#32](https://github.com/LucaCGN/janus_cortex/issues/32) | P0 | Validate controller activation gate against repo-local runtime root. |
| [#33](https://github.com/LucaCGN/janus_cortex/issues/33) | P1 | Validate closed seed foundations against a running Janus API. |
| [#34](https://github.com/LucaCGN/janus_cortex/issues/34) | P1 | Run WNBA minimal-readiness dry run without live orders. |
| [#35](https://github.com/LucaCGN/janus_cortex/issues/35) | P1 | Build read-only global portfolio explorer automation. |
| [#36](https://github.com/LucaCGN/janus_cortex/issues/36) | P2 | Archive or delete absorbed ML replay branch after operator approval. |

## Duplicate Issue Cleanup

Closed on 2026-05-17 as superseded by the canonical `JANUS-P0-*` set:

| Closed Issue | Canonical Replacement |
|---|---|
| https://github.com/LucaCGN/janus_cortex/issues/11 | https://github.com/LucaCGN/janus_cortex/issues/17 |
| https://github.com/LucaCGN/janus_cortex/issues/12 | https://github.com/LucaCGN/janus_cortex/issues/18 |
| https://github.com/LucaCGN/janus_cortex/issues/13 | https://github.com/LucaCGN/janus_cortex/issues/19 |
| https://github.com/LucaCGN/janus_cortex/issues/14 | https://github.com/LucaCGN/janus_cortex/issues/20 |
| https://github.com/LucaCGN/janus_cortex/issues/15 | https://github.com/LucaCGN/janus_cortex/issues/21 |
| https://github.com/LucaCGN/janus_cortex/issues/16 | https://github.com/LucaCGN/janus_cortex/issues/22 |
