# Branch Strategy

## Goals
- keep `main` clean and mergeable at all times
- keep branch scopes narrow enough for parallel work without write collisions
- make it obvious which branch category owns which folders
- delete branches and worktrees immediately after merge and sync

## Branch Categories

| Category | Branch Prefix | Owns | Notes |
| --- | --- | --- | --- |
| Analysis core | `codex/analysis-...` | `app/data/pipelines/daily/nba/analysis/*`, analysis tests, analysis docs | offline mart, reports, backtests, models, research consumers |
| Data platform | `codex/data-...` | `app/data/databases/*`, `app/data/pipelines/daily/*`, data tests, schema docs | migrations, sync pipelines, dev DB safety, season-scope ingestion |
| Frontend | `codex/frontend-...` | future `frontend/*`, consumer-facing contracts, frontend docs | permanent UI only, not sandboxes |
| Ops/runtime | `codex/ops-...` | automation scripts, validation jobs, operator runbooks | daily refresh hardening, validation orchestration, monitoring |
| Season expansion | `codex/season-...` | playoff/preseason/WNBA support across data, analysis, and docs | cross-cuts multiple subsystems after upstream safety gates |
| Documentation | `codex/docs-...` | planning docs, guide docs, repo hygiene docs | use only for doc-only changes |

## Recommended Repository Shape Going Forward
- keep offline research logic under `app/data/pipelines/daily/nba/analysis/*`
- keep read-only consumer adapters under a dedicated backend surface after `A8` approval
- create the permanent frontend under `frontend/analysis_studio/`
- keep local-only branch registers, outputs, and active notes under `JANUS_LOCAL_ROOT`

## Parallel Work Method
1. create every branch from clean `main`
2. run `powershell -ExecutionPolicy Bypass -File .\tools\janus_local.ps1 status`
3. keep one branch focused on one category and one narrow write scope
4. if two branches need the same file, split the work differently before coding
5. merge one lane at a time back into `main`
6. after merge, push `main`, remove the worktree, delete the branch, and archive any local-only notes or outputs

## Integration Rule
- avoid long-lived integration branches unless multiple already-finished lanes must be tested together
- integration branches are temporary and should never become the long-term source of truth
- if a lane is small enough to merge directly into `main` after validation, do that instead

## Current Dependency Ladder
1. `codex/data-dev-db-safety`
   - build the safe local SQLite plus disposable Postgres plus dev-clone workflow
2. `codex/ops-analysis-validation`
   - run full-season mart, report, backtest, and baseline validations on the dev database
3. `codex/analysis-strategy-lab`
   - convert current research outputs into comparable trading-strategy candidates
4. `codex/analysis-sampling-benchmarking`
   - add 5%-10% random-sample holdouts, naive benchmarks, and experiment registry outputs
5. `codex/analysis-a8-consumer-adapters`
   - expose stable read-only contracts for downstream consumers
6. `codex/frontend-analysis-studio`
   - build the dedicated frontend module on top of stable read-only contracts
7. `codex/season-playoffs-preseason`
   - prepare season-scope structures for play-in, playoffs, and preseason
8. `codex/season-wnba-bootstrap`
   - prepare WNBA carry-over and offseason continuity work

## Branch Launch Guidance
- do not launch frontend before read-only consumer contracts stabilize
- do not launch season-expansion branches before dev-db safety and validation hardening are in place
- do not launch strategy-comparison branches before the current offline stack has a clean validation report
