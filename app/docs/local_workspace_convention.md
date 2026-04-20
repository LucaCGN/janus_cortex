# Local Workspace Convention

## Purpose
Keep branch-independent local state outside the repository so worktrees stay disposable and `main` stays clean.

## Default Local Root
- preferred local root: `C:\code-personal\janus-local\janus_cortex`
- optional override: `JANUS_LOCAL_ROOT`

If `JANUS_LOCAL_ROOT` is unset, the repo helper defaults to the path above.

## Layout
- `tracks/`: persistent checkpoint ledgers, reference folders, and branch-independent notes
- `archives/`: generated outputs worth keeping outside the repo root
- `stashes/`: exported stash patches and metadata

Stable subpaths:
- `tracks/dev-checkpoint`
- `tracks/reference`
- `tracks/reference/db`
- `tracks/planning/current`
- `tracks/planning/archive`
- `archives/output`

## What Stays In The Repo
- source code
- committed tests
- committed docs under `app/docs`
- local runtime files that are tied to the active workspace only: `.env`, `.venv`

## What Does Not Stay In The Repo Root
- ad hoc `output/`
- local `reference/`
- local `dev-checkpoint/`
- active branch registers that are not meant to be committed
- `.playwright-cli/`
- `.pytest_cache/`
- stash-only branch snapshots

## Helper Script
Use [tools/janus_local.ps1](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/tools/janus_local.ps1) from the repository root.

Common commands:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\janus_local.ps1 ensure
powershell -ExecutionPolicy Bypass -File .\tools\janus_local.ps1 status
powershell -ExecutionPolicy Bypass -File .\tools\janus_db.ps1 status
powershell -ExecutionPolicy Bypass -File .\tools\janus_local.ps1 track-path -SourcePath .\dev-checkpoint -Name dev-checkpoint
powershell -ExecutionPolicy Bypass -File .\tools\janus_local.ps1 track-path -SourcePath .\reference -Name reference
powershell -ExecutionPolicy Bypass -File .\tools\janus_local.ps1 archive-path -SourcePath .\output -Name output
powershell -ExecutionPolicy Bypass -File .\tools\janus_local.ps1 export-stash -Name branch_cleanup
powershell -ExecutionPolicy Bypass -File .\tools\janus_local.ps1 clean-generated
```

For the analysis CLI, default artifact output resolves to `JANUS_LOCAL_ROOT\archives\output\nba_analysis` when `JANUS_LOCAL_ROOT` is set, or to the standard Windows local root if it exists.

Disposable Postgres env snapshots and DB reference notes should live under `JANUS_LOCAL_ROOT\tracks\reference\db`.

## Parallel Branch Hygiene
Before removing a worktree or deleting a lane branch:
1. Export any stash you want to preserve into `stashes/`.
2. Move ad hoc `output/`, `reference/`, or `dev-checkpoint/` content into the local root.
3. Remove generated caches with `clean-generated`.
4. Confirm `git status` is clean before collapsing the branch or worktree.

## Session Start Checklist
1. Run `powershell -ExecutionPolicy Bypass -File .\tools\janus_local.ps1 status`.
2. Keep new branch-independent notes under the local root, not the repository root.
3. Keep active branch registers and session notes under `tracks/planning/current`.
4. Move superseded local notes to `tracks/planning/archive`.
5. Use committed docs in `app/docs` for canonical project behavior and design decisions.
