# Janus Issue Task Register

Status: active control document
Created: 2026-05-24
Owner issue: #73

## Purpose

GitHub issues are the durable work identity. This register is the local execution bridge that decomposes those issues into bounded tasks the automations can claim, validate, commit, close, split, or deliberately block.

The register exists to stop repeated automation comments from becoming the task tracker. A pass that selects an issue should either work a task listed here, add a missing task, update a task with a changed blocker, or compress as no-change.

## Authority

This file is a repo source-of-truth planning document. It is not live execution truth.

Use it with:
- GitHub issues for priority, labels, acceptance criteria, and closure.
- `python tools/controller_queue.py status` for active lock ownership.
- Runtime artifacts and direct CLOB truth for trading/execution evidence.
- `local/shared/handoffs/development-agent/status.md` for short-lived handoff details.

Update this register only when task decomposition, next executable step, blocker, ownership, or completion evidence materially changes. Do not edit it just to prove an automation ran.

## Task Fields

| Field | Meaning |
|---|---|
| `Task id` | Stable local id `JIT-<issue>-<sequence>`. |
| `Issue` | GitHub issue owning the task. |
| `Status` | `ready`, `active`, `blocked`, `review`, `done`, `defer`. |
| `Owner lane` | Expected automation/persona. |
| `Next executable step` | Smallest concrete next action, not a broad goal. |
| `Evidence / blocker` | Latest material proof, commit, artifact, or exact blocker. |

## Operating Rules

1. Every open P0/P1 issue selected for work should have at least one active or ready task here.
2. Parent architecture issues should normally point to child tasks; do not implement a parent directly if a child exists.
3. During NBA/WNBA live windows, tasks under #61/#62 can stay active while implementation tasks wait unless the implementation is a critical live blocker.
4. A repeated GitHub comment without a changed task state is no progress.
5. A task is `done` only when validation evidence exists and the owning GitHub issue was updated or closure is clearly pending.
6. If a task remains `blocked` for two material passes with no new evidence, oversight-devloop should split it, change acceptance criteria, or mark it operator-blocked.

## Current Execution Plan

### Active Live Evidence Tasks

| Task id | Issue | Status | Owner lane | Next executable step | Evidence / blocker |
|---|---:|---|---|---|---|
| JIT-62-01 | #62 | active | janus-master-dev | Finish the 2026-05-24 WNBA live monitor through final state; capture final score/clock, event-scoped CLOB inventory, intents, blockers, and direct flatness/order proof. | Latest handoff shows WSH/SEA no-order with `llm_revision_unavailable` / `llm_event_budget_exceeded`; current-event inventory flat. |
| JIT-61-01 | #61 | active | janus-master-dev | Keep NBA OKC/SAS under app-owned live worker through start and live phases; capture first live scoreboard transition and any order/fill/blocker evidence. | Pregame blocker remains `scoreboard_freshness_required`; worker and preflight are green. |

### Immediate Post-Live Development Stack

| Task id | Issue | Status | Owner lane | Next executable step | Evidence / blocker |
|---|---:|---|---|---|---|
| JIT-70-01 | #70 | ready | janus-postgame-signal-review / janus-performance-review | Generate a structured postgame signal-performance artifact for the 2026-05-24 NBA/WNBA window: fired triggers, blocked triggers, missed signals, stale books, LLM budget failures, and no-order decisions. | Needed before changing thresholds or closing #61/#62. |
| JIT-68-01 | #68 | ready | janus-master-dev | Implement degraded deterministic fallback so `llm_event_budget_exceeded` or missing pregame priors disable only the LLM/research source, not approved deterministic/ML signal evaluation. | WNBA live ticks repeatedly failed closed on LLM budget/revision availability while runtime/CLOB gates were green. |
| JIT-65-01 | #65 | ready | janus-master-dev | Add a typed live signal schema/persistence path for deterministic scoreboard/CLOB, LLM, Codex/operator, and blocked/missed signals. | Required foundation for aggregator replay and postgame scoring. |
| JIT-66-01 | #66 | ready | janus-master-dev | Build aggregation arbitration over persisted live signals, current inventory, score state, CLOB movement, cooldowns, and blockers. | Current system records microstructure and blockers but does not emit an actionable merged decision. |
| JIT-67-01 | #67 | ready | janus-master-dev | Add tests and runtime examples for event risk cap and sleeve transitions: grid scalp, core hold, rebuy, reduce/stop, monitor-only. | Runtime has two-sleeve plans and $10 cap, but transition logic still needs validation. |
| JIT-69-01 | #69 | ready | janus-master-dev | Define and implement safe event-control read/update endpoints for signal toggles and parameters. | Needed for Codex/operator/LLM to adjust gates without code edits or chat memory. |

### Governance And Automation Tasks

| Task id | Issue | Status | Owner lane | Next executable step | Evidence / blocker |
|---|---:|---|---|---|---|
| JIT-73-01 | #73 | active | oversight-devloop / master-janus-manager | Require issue-task-register review in controller docs and use it to prevent comment-only loops. | This register is the current implementation slice. |
| JIT-75-01 | #75 | blocked | janus-portfolio-manager / oversight-portfolio | Wait for next portfolio-manager run to prove queue claim/release discipline or record an exact blocker; then close #75 if proof is adequate. | 18:06Z artifact set was reconciled in memory/docs, but original queue claim was missing. |
| JIT-71-01 | #71 | ready | janus-performance-review | Verify first scheduled performance-review memory/artifact, then connect it to #70 outputs and issue task updates. | New lane exists; first durable memory still pending in latest checks. |
| JIT-74-01 | #74 | defer | obsidian-backlog-ingestor / janus-obsidian-builder | Run backlog-ingestor dry run after live/core runtime priorities quiet down; list candidate Obsidian notes with acceptance criteria. | P2; no live blocker. |

### Supporting Research And Calibration

| Task id | Issue | Status | Owner lane | Next executable step | Evidence / blocker |
|---|---:|---|---|---|---|
| JIT-55-01 | #55 | ready | nba-pregame-research / postgame-review | Feed #70 postgame evidence into entry-timing research for pregame, immediate-live, post-Q1, and stability-confirmed policies. | Supports #61 but does not own live execution. |
| JIT-72-01 | #72 | ready | nba-pregame-research / wnba-pregame-research | Convert pregame outputs into optional prior artifacts with expiry; prove missing priors become `optional_prior_missing`, not live-disable. | Today already showed live worker can run without relying on pregame as execution truth. |
| JIT-42-01 | #42 | ready | execution / risk-manager | Reconcile CLOB/API and UI minimum-size behavior, then document the urgent-profit market-order exception as disabled-by-default. | Should follow core live no-order blockers. |
| JIT-44-01 | #44 | defer | risk-ledger-agent | Calibrate profit-ratcheted risk ladder after more realized event/live-test data exists. | Needs realized data from #61/#62/#70. |
| JIT-46-01 | #46 | defer | profile-research-agent | Convert tracked winning profile notes into benchmark hypotheses with caveats and validation requirements. | Future-domain/portfolio support, not live blocker. |
| JIT-47-01 | #47 | defer | future-domain-research-agent | Keep crypto up/down as research/backtest only until basketball runtime signal stack is reliable. | Do not expand before #65/#66/#68/#70. |
| JIT-48-01 | #48 | defer | future-domain-research-agent | Keep geopolitics/economics/culture lanes as watch/research backlog with no execution authority. | Do not preempt covered NBA/WNBA runtime. |

## Completion Stack After Today's Runs

When the current live window is no longer active, work should proceed in this order unless fresh runtime evidence changes priorities:

1. Complete JIT-70-01 postgame signal-performance artifact.
2. Implement JIT-68-01 deterministic fallback.
3. Implement JIT-65-01 signal schema/persistence.
4. Implement JIT-66-01 aggregation arbitration.
5. Validate JIT-67-01 sleeve/risk transitions.
6. Implement JIT-69-01 runtime event-control endpoints.
7. Use JIT-71-01 project-chief review to score results and update this register.

Portfolio and future-domain tasks should not preempt this stack unless they expose a direct live-money safety issue.
