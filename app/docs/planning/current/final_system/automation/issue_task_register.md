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
| JIT-62-01 | #62 | done | janus-master-dev | Final-state WNBA live-monitor evidence captured; keep #62 open for the first controlled WNBA fill-confirmed minimum-size lifecycle. | Commit `c6029a5` added `app/docs/reference/postgame_evaluation_2026-05-24_wnba_live_gap.md`: all three scoped WNBA events ended flat with no positions, no orders, no direct trades, `pending_intents=0`, and final `orderbook_spread_required` blockers. |
| JIT-61-01 | #61 | active | janus-master-dev | Keep NBA OKC/SAS under app-owned live worker through final/protect-only phases; validate, commit/push, split, or exactly block any dirty live-target follow-up patch before unrelated implementation. | 2026-05-25T01Z manual validation fixed stale pending-intent expiry and strategy-owned target protection, then live worker executed 10 Thunder shares at 0.22 and submitted sell targets. The 2026-05-25T02:45Z manager pass found an unowned dirty follow-up patch in live target-basis/tick-size code and tests; route it to #61 cleanup, not a passive blocker. |
| JIT-62-02 | #62 | done | janus-master-dev | Use the next WNBA pregame route to generate/adopt a current WNBA StrategyPlan with `wnba_controlled_min_size_entry_v1`, then validate it through rehearsal/live preflight before any live worker start. | Commit `c9541a7` added WNBA controlled-entry evaluator handling, WNBA live-plan fallback sleeves, and tests proving the fallback fires after matching grid spread blockers, caps to one event candidate, and augments the three 2026-05-24 WNBA StrategyPlans. GitHub #62 comment `4531234037` records validation. |

### Immediate Post-Live Development Stack

| Task id | Issue | Status | Owner lane | Next executable step | Evidence / blocker |
|---|---:|---|---|---|---|
| JIT-70-01 | #70 | review | janus-postgame-signal-review / janus-performance-review | Use the completed 2026-05-24T2328Z artifact to route replay/config work; rerun only after WSH/SEA or OKC/SAS reaches final, or after a new replay/config issue is created. | `postgame_signal_review_2026-05-24T2328Z.md` and GitHub #70 comment identify the WNBA `score_gap` null blocker plus Atlanta/Dallas replay candidates. |
| JIT-68-01 | #68 | done | janus-master-dev | Closed after deterministic fallback evidence proved LLM/research unavailability is not a deterministic sleeve live blocker when required runtime gates are green. | Implemented in `codex_tool/run_live_strategy_tick.py`: LLM revision unavailability is advisory unless the plan/sleeve explicitly requires LLM review; stale local pending rows expire after event start when direct CLOB has no matching open/fill. Validated with live-tick and StrategyPlan regression tests plus the 2026-05-25T01Z #61 live window. |
| JIT-65-01 | #65 | done | janus-master-dev | Typed live signal schema and runtime artifact persistence path implemented; keep #65 open only if reviewer wants DB-backed storage before #66. | Added `LiveSignal` contracts and `write_live_signals` artifact persistence, with deterministic/Codex sample artifacts under `local/shared/artifacts/live-signals/2026-05-25/`; validation passed `python -m pytest tests/app/modules/agentic/test_live_signal_contracts_pytest.py tests/app/modules/agentic/test_strategy_plan_contracts_pytest.py -q` (`31 passed`) and `python -m compileall app/modules/agentic/contracts.py app/modules/agentic/store.py`. |
| JIT-66-01 | #66 | ready | janus-master-dev | Build aggregation arbitration over persisted live signals, current inventory, score state, CLOB movement, cooldowns, and blockers. | Current system records microstructure and blockers but does not emit an actionable merged decision. |
| JIT-67-01 | #67 | ready | janus-master-dev | Add tests and runtime examples for event risk cap and sleeve transitions: grid scalp, core hold, rebuy, reduce/stop, monitor-only. | Runtime has two-sleeve plans and $10 cap, but transition logic still needs validation. |
| JIT-69-01 | #69 | ready | janus-master-dev | Define and implement safe event-control read/update endpoints for signal toggles and parameters. | Needed for Codex/operator/LLM to adjust gates without code edits or chat memory. |

### Governance And Automation Tasks

| Task id | Issue | Status | Owner lane | Next executable step | Evidence / blocker |
|---|---:|---|---|---|---|
| JIT-73-01 | #73 | active | oversight-devloop / master-janus-manager | Require issue-task-register review in controller docs and use it to prevent comment-only loops. | This register is the current implementation slice. |
| JIT-75-01 | #75 | done | janus-portfolio-manager / oversight-portfolio | Closed after portfolio-manager proved queue claim/release discipline; keep future portfolio drift or expansion out of closed #75. | Queue lock `janus-portfolio-manager-20260525T000504Z-deep-pass` released at `2026-05-25T00:34:38Z` with durable memory, runtime artifacts, and Obsidian portfolio updates. |
| JIT-76-01 | #76 | active | janus-portfolio-manager / oversight-portfolio | Claim #76 global-portfolio scope, refresh direct truth, place or block the Maduro `SELL 5 @ 0.700` target through approved portfolio-manager gates, confirm Colorado close/fill evidence, and update artifacts/memory/Obsidian. | Oversight-portfolio 2026-05-25T02:32Z classified this RED: Maduro is filled without a live target, Colorado is absent from current direct open positions/orders, GitHub #76 is open, and portfolio Obsidian notes were updated. |
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
| JIT-47-01 | #47 | defer | future-domain-research-agent | Keep crypto up/down as research/backtest only until basketball runtime signal stack is reliable. | Do not expand before #65/#66/#70 and fresh postgame evidence. |
| JIT-48-01 | #48 | defer | future-domain-research-agent | Keep geopolitics/economics/culture lanes as watch/research backlog with no execution authority. | Do not preempt covered NBA/WNBA runtime. |

## Completion Stack After Today's Runs

When the current live window is no longer active, work should proceed in this order unless fresh runtime evidence changes priorities:

1. Use the next WNBA pregame route under #62 to generate/adopt a current controlled-entry StrategyPlan and validate preflight gates before any live worker start.
2. Use JIT-70-01 evidence to route replay/config work from the NBA/WNBA postgame docs.
3. Implement JIT-66-01 aggregation arbitration.
4. Validate JIT-67-01 sleeve/risk transitions.
5. Implement JIT-69-01 runtime event-control endpoints.
6. Use JIT-71-01 project-chief review to score results and update this register.

Portfolio and future-domain tasks should not preempt this stack unless they expose a direct live-money safety issue.
