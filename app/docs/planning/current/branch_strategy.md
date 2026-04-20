# Branch Strategy

## Goals
- keep `main` clean and mergeable at all times
- keep branch scopes narrow enough for parallel work without write collisions
- make it obvious which branch category owns which folders
- delete branches and worktrees immediately after merge and sync

## Branch Categories

| Category | Branch Prefix | Owns | Notes |
| --- | --- | --- | --- |
| Analysis core | `codex/analysis-...` | `app/data/pipelines/daily/nba/analysis/*`, analysis tests, analysis docs | offline mart, reports, backtests, models, strategy refinement, sequential portfolio benchmarking |
| Data platform | `codex/data-...` | `app/data/databases/*`, `app/data/pipelines/daily/*`, data tests, schema docs | migrations, sync pipelines, dev DB safety, season-scope ingestion |
| Frontend | `codex/frontend-...` | future `frontend/*`, consumer-facing contracts, frontend docs | permanent UI only, not sandboxes |
| Ops/runtime | `codex/ops-...` | automation scripts, validation jobs, operator runbooks | daily refresh hardening, validation orchestration, monitoring |
| Season expansion | `codex/season-...` | playoff/preseason/WNBA support across data, analysis, and docs | cross-cuts multiple subsystems after upstream safety gates |
| Documentation | `codex/docs-...` | planning docs, guide docs, repo hygiene docs | use only for doc-only changes |

## Recommended Repository Shape Going Forward
- keep offline research logic under `app/data/pipelines/daily/nba/analysis/*`
- keep sequential portfolio simulation under the analysis core lane rather than in frontend code
- keep read-only consumer adapters under a dedicated backend surface after `A8` approval
- create the permanent frontend under `frontend/analysis_studio/`
- keep local-only branch registers, outputs, and active notes under `JANUS_LOCAL_ROOT`

## Parallel Work Method
1. create independent branches from clean `main`
2. create a stacked follow-on branch only when a branch plan explicitly depends on an unmerged branch
3. document the stacked base branch in the branch plan and PR body before coding
4. run `powershell -ExecutionPolicy Bypass -File .\tools\janus_local.ps1 status`
5. keep one branch focused on one category and one narrow write scope
6. if two branches need the same file, split the work differently before coding
7. merge one lane at a time back into `main`
8. after merge, push `main`, remove the worktree, delete the branch, and archive any local-only notes or outputs

## Integration Rule
- avoid long-lived integration branches unless multiple already-finished lanes must be tested together
- integration branches are temporary and should never become the long-term source of truth
- if a lane is small enough to merge directly into `main` after validation, do that instead

## Completed And Archived Branches
- `codex/data-dev-db-safety`
- `codex/ops-analysis-validation`
- `codex/analysis-strategy-lab`
- `codex/analysis-sampling-benchmarking`
- `codex/analysis-a8-consumer-adapters`
- `codex/frontend-analysis-studio`
- `codex/analysis-backtest-detail-contract`
- `codex/frontend-analysis-comparison`

## Current Dependency Ladder
1. `codex/analysis-sequential-portfolio-benchmarking`
   - rerun each strategy family under a linear bankroll path and freeze the surviving candidates
2. `codex/season-playoffs-preseason`
   - prepare season-scope structures for play-in, playoffs, and preseason
3. `codex/season-wnba-bootstrap`
   - prepare WNBA carry-over and offseason continuity work

Detailed subphase plans live under:
- [branches/README.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/README.md)

## Branch Launch Guidance
- do not launch sequential portfolio benchmarking before the sampling benchmark contract is stable on `main`
- do not let sequential bankroll accounting change the underlying strategy-family math without a separate branch and explicit doc update
- do not launch season-expansion branches before dev-db safety and validation hardening are in place
- do not launch new strategy-refinement branches before the current offline stack has a clean validation report
