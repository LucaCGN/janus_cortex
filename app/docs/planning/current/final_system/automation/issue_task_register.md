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
| JIT-64-01 | #64 | done | janus-master-dev | First normalized NBA/WNBA live snapshot artifact and tests implemented; use follow-up #64/#63 work to wire the snapshot object into live tick output and API surfaces. | 2026-05-25T06:23Z artifact `local/shared/artifacts/normalized-live-snapshots/2026-05-25/normalized_live_snapshot_review_20260525T062318Z.json` records one NBA and one WNBA `normalized_live_snapshot_v1` sample with shared game/feed/CLOB/account/runtime/evidence fields and league-scoped blocker codes. Validation passed `python -m pytest tests/app/modules/agentic/test_live_snapshot_pytest.py -q` (`4 passed`) and `python -m compileall app/modules/agentic/live_snapshot.py codex_tool/run_live_snapshot_review.py`. |
| JIT-64-02 | #64 | done | janus-master-dev | Normalized live snapshot object wired into live strategy tick event output and shadow evaluation market-state evidence. Use future #64/#63 work to expose the same object from API review surfaces if needed. | Each `_run_event_tick` now includes `normalized_live_snapshot_v1` built from game/live feed, sampled CLOB, event-scoped direct CLOB inventory, StrategyPlan gate, worker status, and evidence paths. Validation passed `python -m pytest tests/tools/test_run_live_strategy_tick_pytest.py tests/app/modules/agentic/test_live_snapshot_pytest.py tests/app/modules/agentic/test_strategy_plan_contracts_pytest.py -q` (`83 passed`) and compileall for the touched modules. |
| JIT-70-01 | #70 | review | janus-postgame-signal-review / janus-performance-review | Use the completed 2026-05-24T2328Z artifact to route replay/config work; rerun only after WSH/SEA or OKC/SAS reaches final, or after a new replay/config issue is created. | `postgame_signal_review_2026-05-24T2328Z.md` and GitHub #70 comment identify the WNBA `score_gap` null blocker plus Atlanta/Dallas replay candidates. |
| JIT-70-02 | #70 | done | janus-master-dev | Fix `/v1/events/{event_id}/review-bundle` HTTP 500 `TypeError` so project-chief review can consume structured event bundles. | Commit `f1f73ec`: `_build_event_review_bundle` now passes `day=session_date` into postgame portfolio PnL attribution; `tests/app/api/test_ops_router_pytest.py` asserts the day is forwarded. Validation passed `45 passed`, compileall, direct bundle generation for WSH/SEA and OKC/SAS, and `git diff --check` with CRLF warnings only. |
| JIT-70-03 | #70 | done | janus-master-dev / janus-postgame-signal-review | WNBA outcome-level `score_gap` derivation repaired; use the next #70/#55 slice to convert replay candidates into structured entry-timing cases or config recommendations. | 2026-05-25T05:23Z master-dev pass: `_scoreboard_state` now matches WNBA outcome labels through normalized team/tricode aliases; regression tests cover PHX/ATL Atlanta, DAL/NYL Dallas, and WSH/SEA Seattle replay candidates. Historical replay probe showed old persisted outcome gaps were null while patched gaps derive Atlanta `+2`, Dallas `+15`, Seattle `-7` at Q1 candidate and Seattle `+2` at end-Q1 conversion. Validation passed `python -m pytest tests/tools/test_run_live_strategy_tick_pytest.py tests/app/modules/agentic/test_strategy_plan_contracts_pytest.py -q` (`78 passed`) and `python -m compileall codex_tool/run_live_strategy_tick.py`. |
| JIT-70-04 | #70 | done | janus-master-dev | Structured postgame replay/config artifact generated from the repaired WNBA score-gap normalizer and the final OKC/SAS negative replay case. Use JIT-70-05 for fixture replay/backtest implementation before any live StrategyPlan/config promotion. | 2026-05-25T05:36Z artifact `local/shared/artifacts/postgame-replay-config-review/2026-05-25/postgame_replay_config_review_20260525T053648Z.json` records four cases: Atlanta comeback low-band, Dallas Q2 low-band, Seattle Q1 rebound, and OKC/SAS Thunder Q4 subpenny negative. The report recommends keeping WNBA max price at `0.45` until replay, quarantining `q4_subpenny_hype_bounce` / `no_bid_min_price_lottery_v1` until replay proves edge, feeding #55 entry-timing rows, and targeting future toggles at #69 event-control artifacts. |
| JIT-70-05 | #70 | done | janus-master-dev / postgame-review | Fixture replay/backtest coverage built for the four `postgame_replay_config_review_v1` cases, including fillability, score-gap, target-fill costs, duplicate-intent cooldown, and final-score outcome accounting. | 2026-05-25T05:51Z artifact `local/shared/artifacts/postgame-replay-config-review/2026-05-25/postgame_replay_fixture_backtest_20260525T055152Z.json` scores Atlanta, Dallas, and Seattle as positive entry-timing matrix candidates only, keeps `live_promotion_allowed=false`, and quarantines OKC/SAS Thunder Q4 subpenny behavior because duplicate cooldown and final-score negative-edge blockers remain. |
| JIT-68-01 | #68 | done | janus-master-dev | Closed after deterministic fallback evidence proved LLM/research unavailability is not a deterministic sleeve live blocker when required runtime gates are green. | Implemented in `codex_tool/run_live_strategy_tick.py`: LLM revision unavailability is advisory unless the plan/sleeve explicitly requires LLM review; stale local pending rows expire after event start when direct CLOB has no matching open/fill. Validated with live-tick and StrategyPlan regression tests plus the 2026-05-25T01Z #61 live window. |
| JIT-65-01 | #65 | done | janus-master-dev | Typed live signal schema and runtime artifact persistence path implemented; keep #65 open only if reviewer wants DB-backed storage before #66. | Added `LiveSignal` contracts and `write_live_signals` artifact persistence, with deterministic/Codex sample artifacts under `local/shared/artifacts/live-signals/2026-05-25/`; validation passed `python -m pytest tests/app/modules/agentic/test_live_signal_contracts_pytest.py tests/app/modules/agentic/test_strategy_plan_contracts_pytest.py -q` (`31 passed`) and `python -m compileall app/modules/agentic/contracts.py app/modules/agentic/store.py`. |
| JIT-66-01 | #66 | done | janus-master-dev | First-pass live signal aggregation arbitration implemented; use #67/#69 to connect event budgets, sleeve transitions, and runtime controls before live-worker adoption. | Added `app/modules/agentic/signal_aggregation.py` with event-scoped aggregation decisions, stale/duplicate/conflict/inventory/budget blockers, order-intent candidates, and artifact persistence via `write_live_signal_aggregation_decision`; validation passed `python -m pytest tests/app/modules/agentic/test_signal_aggregation_pytest.py tests/app/modules/agentic/test_live_signal_contracts_pytest.py tests/app/modules/agentic/test_strategy_plan_contracts_pytest.py -q` (`36 passed`) and `python -m compileall app/modules/agentic/signal_aggregation.py app/modules/agentic/store.py app/modules/agentic/contracts.py`. |
| JIT-67-01 | #67 | done | janus-master-dev | Event budget and sleeve transition helper implemented; use `#69` to expose validated runtime control reads/updates before live-worker adoption. | Added `app/modules/agentic/event_budget.py` with percentage-derived cap snapshots and decisions for grid scalp, core hold, rebuy, reduce/stop, monitor-only, duplicate exposure, and budget overflow. Validation passed `python -m pytest tests/app/modules/agentic/test_event_budget_pytest.py tests/app/modules/agentic/test_signal_aggregation_pytest.py tests/app/modules/agentic/test_live_signal_contracts_pytest.py tests/app/modules/agentic/test_strategy_plan_contracts_pytest.py -q` (`40 passed`) and `python -m compileall app/modules/agentic/event_budget.py app/modules/agentic/signal_aggregation.py app/modules/agentic/contracts.py`. |
| JIT-69-01 | #69 | done | janus-master-dev | Runtime event-control artifact model and API read/update endpoints implemented; use #70/#71 to consume these controls from postgame review and project-chief recommendations. | Added `app/modules/agentic/runtime_control.py` and `/v1/runtime/event-controls/{event_id}` with attributable updates, safe-cap validation, aggregation-control readback, and tests. Validation passed `python -m pytest tests/app/modules/agentic/test_runtime_control_pytest.py tests/app/api/test_runtime_control_router_pytest.py tests/app/modules/agentic/test_event_budget_pytest.py tests/app/modules/agentic/test_signal_aggregation_pytest.py tests/app/modules/agentic/test_live_signal_contracts_pytest.py tests/app/modules/agentic/test_strategy_plan_contracts_pytest.py tests/app/api/test_ops_router_pytest.py -q` (`90 passed`) plus compileall; runtime artifact `local/shared/artifacts/event-controls/2026-05-25/issue69-runtime-control-validation/current.json` records update/readback proof. |

### Governance And Automation Tasks

| Task id | Issue | Status | Owner lane | Next executable step | Evidence / blocker |
|---|---:|---|---|---|---|
| JIT-73-01 | #73 | active | oversight-devloop / master-janus-manager | Require issue-task-register review in controller docs and use it to prevent comment-only loops. | This register is the current implementation slice. |
| JIT-75-01 | #75 | done | janus-portfolio-manager / oversight-portfolio | Closed after portfolio-manager proved queue claim/release discipline; keep future portfolio drift or expansion out of closed #75. | Queue lock `janus-portfolio-manager-20260525T000504Z-deep-pass` released at `2026-05-25T00:34:38Z` with durable memory, runtime artifacts, and Obsidian portfolio updates. |
| JIT-76-01 | #76 | done | janus-portfolio-manager / oversight-portfolio | Closed after #76 restored Maduro target coverage and reconciled Colorado lifecycle through the approved portfolio-manager path; keep future portfolio lifecycle drift in a focused follow-up issue. | Portfolio-manager lock `janus-portfolio-manager-20260525T060545Z-deep-pass` released with outcome `execution_performed_via_approved_portfolio_manager_path`, artifact `local/shared/artifacts/global-portfolio-manager/2026-05-25/portfolio_manager_pass_final_20260525T061631Z.json`, and GitHub #76 was closed as completed. |
| JIT-71-01 | #71 | done | janus-performance-review / janus-master-dev | Project-chief review contract and deterministic artifact generator implemented; next project-chief pass can use the generated artifact to route #70/#55/#69 work. | Added `app/docs/planning/current/final_system/automation/project_chief_performance_review_contract.md`, `app/modules/agentic/performance_review.py`, and `codex_tool/run_project_chief_review.py`; generated `local/shared/artifacts/project-chief-review/2026-05-25/project_chief_review_20260525T045520Z.json` and `local/shared/reports/daily-live-validation/project_chief_review_20260525T045520Z.md`. Validation passed focused performance-review tests and compileall. |
| JIT-74-01 | #74 | defer | obsidian-backlog-ingestor / janus-obsidian-builder | Run backlog-ingestor dry run after live/core runtime priorities quiet down; list candidate Obsidian notes with acceptance criteria. | P2; no live blocker. |

### Supporting Research And Calibration

| Task id | Issue | Status | Owner lane | Next executable step | Evidence / blocker |
|---|---:|---|---|---|---|
| JIT-55-01 | #55 | done | janus-master-dev / postgame-review | Entry-timing matrix artifact generated from #70 fixture evidence for pregame, immediate-live, post-Q1, and stability-confirmed policies. | 2026-05-25T06:06Z artifact `local/shared/artifacts/entry-timing-research/2026-05-25/entry_timing_matrix_20260525T060635Z.json` records six rows: three eligible WNBA low-band candidates, one OKC/SAS Thunder Q4 subpenny negative bucket, and two policy baselines for pregame expiry and post-Q1 stability. Live promotion remains false. |
| JIT-55-02 | #55 | done | janus-master-dev / basketball-intelligence | Side-by-side policy window accounting added to the entry-timing matrix for pregame resting, first-live, post-Q1, and post-Q1-stability policies. | 2026-05-25T06:52Z artifact `local/shared/artifacts/entry-timing-research/2026-05-25/entry_timing_matrix_20260525T065208Z.json` records 16 side-by-side policy results across four fixture cases and separates return, fill rate, cancellation/expiry, missed-entry cost, adverse selection, and avoided-loss accounting. Validation passed `python -m pytest tests/app/modules/agentic/test_entry_timing_research_pytest.py tests/app/modules/agentic/test_replay_config_review_pytest.py -q` (`6 passed`) and compileall. |
| JIT-55-03 | #55 | done | basketball-intelligence / postgame-review | Real price-path and order-lifecycle replay attached to the entry-timing matrix for post-Q1 and post-Q1-stability windows. | 2026-05-25T07:10Z artifact `local/shared/artifacts/entry-timing-research/2026-05-25/entry_timing_matrix_20260525T071037Z.json` consumes `live-strategy-worker/2026-05-24/ticks.jsonl`, scores four fixture cases against real per-side orderbook paths, finds entry-price fills for all four, stable post-Q1 fills for Atlanta/Dallas/Thunder, only non-stable Seattle, and records NBA Thunder event-start expired-order lifecycle evidence. Live promotion remains false. |
| JIT-55-04 | #55 | ready | basketball-intelligence / postgame-review | Convert the real price-path replay into a read-only config recommendation pack for #69 event-control review, preserving WNBA low-band candidates and Thunder subpenny quarantine. | Needs no live worker start or StrategyPlan template promotion; should emit candidate thresholds/blockers only, with explicit operator/Janus gate requirement before any event-control change. |
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
6. Use the new project-chief review artifact to work #70 WNBA score-gap blockers, feed #55 entry-timing research cases, and target future config recommendations through #69 event-control readbacks.

Portfolio and future-domain tasks should not preempt this stack unless they expose a direct live-money safety issue.
