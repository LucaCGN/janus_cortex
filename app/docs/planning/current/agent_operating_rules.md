# Agent Operating Rules

## Session Start
1. run `powershell -ExecutionPolicy Bypass -File .\tools\janus_local.ps1 status`
2. read [app/docs/planning/README.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/README.md)
3. read the current branch strategy and next-steps docs before creating or reusing a branch
4. read the specific branch plan under `app/docs/planning/current/branches/` before starting implementation
5. confirm whether the task belongs to `analysis`, `data`, `frontend`, `ops`, `season`, or `docs`

## Database Safety Ladder
- always validate data-shape logic locally before touching shared databases
- stage 1: DataFrame fixtures, unit tests, or SQLite-compatible local validation
- stage 2: disposable local Postgres created from current migrations
- stage 3: dev clone of the live database for realistic migration and query validation
- stage 4: shared live database only after stages 1-3 pass
- SQLite is for logic safety, not for proving Postgres DDL compatibility
- never run new migrations on the live database first
- never point `JANUS_RUN_DB_TESTS=1` at the live database first

## Migration Rules
- every migration branch needs a clear target phase and matching schema-doc update
- validate `python -m app.data.databases.migrate --list` before applying anything
- inspect the current DB target with `python -m app.data.databases.migrate --describe-target`
- prefer `powershell -ExecutionPolicy Bypass -File .\tools\janus_db.ps1 reset-disposable` before DB integration tests
- run migration tests on a disposable Postgres database before a dev-clone validation
- keep a rollback or restore note in the branch plan before any shared-db apply
- if a migration affects large existing tables, validate runtime on a dev clone before merge approval
- after DB safety is green, use `powershell -ExecutionPolicy Bypass -File .\tools\run_analysis_validation.ps1 -Target disposable` as the default non-live validation sweep

## Analysis Research Rules
- reports, backtests, and models consume mart outputs only
- no raw ingest-table SQL inside report, backtest, or model branches unless the task is explicitly a mart or bundle-loader change
- keep descriptive outputs visible for unresolved and descriptive-only games; do not hide QA residuals
- treat A6-style baselines as validation scaffolding until they beat naive comparators and survive slippage stress

## Strategy Validation Rules
- every new strategy family must be compared against:
  - naive probability or base-rate baselines
  - simple winner-prediction style baselines where relevant
  - slippage-adjusted returns
- keep a random sample holdout in the 5%-10% range alongside time-based validation
- do not promote a strategy only because it looks good on one narrative team or one narrow game slice
- require visual and tabular feedback for entry, exit, MFE, MAE, and hold-time behavior before promotion

## Frontend Rules
- permanent UI work belongs in `frontend/analysis_studio/`, not in `app/sandboxes`
- frontend branches consume read-only contracts and stable artifacts
- do not let UI needs force direct reads from raw ingest tables

## Branch Hygiene Rules
- one branch, one narrow write scope
- keep local-only notes, branch registers, and outputs under `JANUS_LOCAL_ROOT`
- after merge, export any stash worth keeping, archive local notes, then delete the worktree and branch
- if a branch is blocked by another branch's file ownership, stop and re-scope instead of merging conflicts late

## Documentation Rules
- stable product behavior lives in committed repo docs
- active branch planning lives under `app/docs/planning/current`
- historical execution rationale is listed under `app/docs/planning/archive`
- local, non-committed branch tracking lives under `JANUS_LOCAL_ROOT\tracks\planning`
