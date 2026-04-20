# Branch Plan: `codex/frontend-analysis-studio`

## Role
Permanent frontend branch for the analysis program.

## Target Milestone
- `v1.3.0`

## Depends On
- `codex/analysis-a8-consumer-adapters`

## Owns
- dedicated frontend module
- operator workflows for triggering and reviewing analysis runs
- visual inspection of strategies, models, and game context

Likely write scope:
- `frontend/analysis_studio/*`
- frontend-specific docs
- read-only consumer integration

## Does Not Own
- raw ingest or DB migration logic
- backtest engine math
- sandbox-only prototypes as the long-term solution

## Subphases

### `F1` Frontend Module Scaffold
Objective:
- create the permanent frontend module outside `app/sandboxes`

### `F2` Run Control Surface
Objective:
- let operators trigger and monitor mart, report, backtest, and model runs

### `F3` Game Context Explorer
Objective:
- display game-level and state-level context with analysis overlays

### `F4` Strategy Comparison Views
Objective:
- surface per-strategy leaderboards, trade traces, MFE/MAE, and hold-time distributions

### `F5` Operator UX Hardening
Objective:
- refine the workflows for repeated research usage, not one-off demos

## Merge Gate
- permanent frontend module exists
- frontend reads only stable read-only contracts
- operators can visually compare strategy families
