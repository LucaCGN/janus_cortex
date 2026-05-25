# Janus Project-Chief Performance Review Contract

Status: active draft contract
Owner issue: #71
Created: 2026-05-25

## Purpose

The `janus-performance-review` lane is the read-only project-chief loop for Janus. It turns postgame signal review, live-worker evidence, StrategyPlans, runtime controls, manual-intervention notes, pregame priors, issue state, and curated lessons into a ranked improvement plan.

This lane does not execute trades. It exists to make the next development and configuration work concrete.

## Authority

Inputs, in authority order:

1. Direct CLOB/order/fill/position evidence captured by Janus artifacts.
2. Janus DB/API and StrategyPlan versions.
3. Runtime artifacts under `local/shared/artifacts`.
4. Runtime reports under `local/shared/reports`.
5. Runtime handoffs.
6. Tracked repo docs and GitHub issues.
7. Obsidian curated notes.

The lane may recommend issue actions, config review, replay tasks, or closures. It may not treat Obsidian, GitHub comments, or automation memory as live trading truth.

## Cadence And Prompt

Codex automation config:

`C:\Users\lnoni\.codex\automations\janus-performance-review\automation.toml`

Expected cadence: daily at 06:30 BRT, plus manual bounded runs when the master controller needs a project-chief artifact.

The prompt must remain a short pointer to repo source-of-truth docs, especially `#71`, `#70`, `source_of_truth_map.md`, `live_runtime_scope_map_2026-05-24.md`, `janus_core_live_trading_runtime.md`, and this contract.

## Output Schema

The current deterministic artifact schema is `project_chief_performance_review_v1`, written by:

```powershell
python codex_tool\run_project_chief_review.py --session-date <YYYY-MM-DD> --json
```

Required output groups:

- `strategy_score_deltas`
- `missed_opportunity_summary`
- `technical_blockers`
- `config_recommendations`
- `issue_actions`
- `next_priority_queue`
- `hard_prohibitions`

Artifacts are stored under:

- `local/shared/artifacts/project-chief-review/<session-date>/`
- `local/shared/reports/daily-live-validation/project_chief_review_<timestamp>.md`

## Hard Prohibitions

- Do not place, cancel, replace, submit, sign, broadcast, redeem, or prepare orders.
- Do not start, stop, or reconfigure live-money workers.
- Do not bypass Janus StrategyPlan, evaluate, execute, live-worker, or direct-truth gates.
- Do not use raw exchange bypass.
- Do not promote a strategy solely from a narrative comment; require replay, direct evidence, or a bounded issue/config action.

## Completion Criteria

Issue #71 can close when:

- this contract is tracked;
- the Codex automation prompt exists or this tracked prompt can recreate it;
- the deterministic artifact generator produces the required schema;
- at least one generated review artifact links postgame evidence to issue/config priorities;
- GitHub #71 records validation evidence.
