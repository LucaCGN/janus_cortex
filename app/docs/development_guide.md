# Janus Cortex Development Guide (Persistent Session Handoff)

## Purpose
This guide keeps continuity across sessions and agents.
It defines:
- project context,
- current phase,
- how to execute work,
- how to update structure and docs,
- mandatory synchronization between checkpoints, schema, and routes.

## Project context
Janus Cortex v1 focus is data platform readiness, not autonomous strategy generation.

v1 target runtime:
- Postgres (primary DB)
- ChromaDB (event document collections)
- FastAPI (data/service API)
- all services in docker

v1 scope:
- schema and pipelines stable,
- routes complete for ingestion and read/serve,
- manual dev scripts to validate behavior with selected NBA games/events,
- no production auto-trader logic inside core app.

## Current phase
- Active phase: `v0.3.4`
- Reference source of truth: `dev-checkpoint/v0.3.4.md`

## Canonical planning files
1. `app/docs/scalable_db_schema_proposal.md`
2. `app/docs/scalable_api_routes_proposal.md`
3. `dev-checkpoint/README.md`
4. `dev-checkpoint/v0.X.Y.md` (phase checkpoints)
5. `app/docs/source_temporal_coverage.md`
6. `app/docs/development_guide.md` (this file)
7. `app/docs/app_structure_modularization_plan.md`

## Pre-v0.3 structure gate
The pre-`v0.3` structure gate is completed and documented in:
- `app/docs/app_structure_modularization_plan.md`

Core principle:
- providers (`app/providers/*`) and event categories (`app/domain/events/categories/*`) must be independent axes.
- NBA is a category/module implementation, not the app root structure.

Gate status:
- `v0.2.7` completed (structure boundaries + compatibility wrappers)
- `v0.2.8` completed (pytest topology + regression validation)
- `v0.2.9` completed (doc/checkpoint synchronization)

## Execution model
1. Read active checkpoint file first.
2. Implement only work in scope for active phase.
3. Create/update pytest modules for every refactored node method in scope.
4. Run tests listed in checkpoint.
5. If phase completes, update phase status and advance to next phase.
6. Never activate routes/tables before dependency phase is complete.

## Required update protocol (must do on every meaningful change)

### A. If schema changed
Update all:
- `app/docs/scalable_db_schema_proposal.md`
- current checkpoint file (`dev-checkpoint/v0.X.Y.md`)
- `dev-checkpoint/README.md` (phase progression notes)

Also update table relation fields:
- table name
- column list
- activation phase
- migration id/script reference

### B. If route changed
Update all:
- `app/docs/scalable_api_routes_proposal.md`
- current checkpoint file
- tests section in checkpoint with endpoint validation

Also update route relation fields:
- method + path
- activation phase
- required tables
- test script path

### C. If node method changed (`app/data/nodes/*`)
Update all:
- current checkpoint file method inventory section
- `dev-checkpoint/README.md` notes if phase objective changed
- add/adjust test commands and expected outputs in checkpoint
- create/update dedicated pytest module(s) for changed method(s)
- record pytest execution evidence in checkpoint result section
- update temporal coverage matrix in `app/docs/source_temporal_coverage.md` (past/current/future availability + limits)

## Node Method Testing Rule (Non-Negotiable)
For each node method refactor or new method:
1. Create or update a dedicated `pytest` module.
2. Cover positive path, malformed input, and source-unavailable fallback behavior.
3. Execute pytest in the same phase/subphase where method work happened.
4. Record command(s) and result evidence in the active checkpoint file.

Recommended naming convention:
- `*_pytest.py` (repository `pytest.ini` only collects this suffix)
- place pytest modules under top-level `tests/app/...` mirroring the source path under `app/...`
- one or more test functions per method variant
- legacy manual scripts must not use pytest discovery names; keep them under `legacy_*` naming or outside testpaths

Execution conventions:
- Default (offline-safe) run: `python -m pytest -q`
- Live source validation run: `$env:JANUS_RUN_LIVE_TESTS='1'; python -m pytest -q <module_or_pattern>`
- `live_api` tests are skipped unless `JANUS_RUN_LIVE_TESTS=1`.
- Postgres integration validation run: `$env:JANUS_RUN_DB_TESTS='1'; python -m pytest -q tests/app/data/databases/test_postgres_migrations_pytest.py`
- `postgres_live` tests are skipped unless `JANUS_RUN_DB_TESTS=1`.

## Temporal Coverage Validation Rule (Non-Negotiable)
For data-source methods with time dimension (events, schedules, odds, live feeds):
1. Validate and document coverage for:
   - past data,
   - current data,
   - future/scheduled data.
2. Record observed limitations and failure modes.
3. Update `app/docs/source_temporal_coverage.md` in the same subphase.

## Folder and structure rules

### Data nodes first rule
Before DB or API expansion, validate methods under:
- `app/data/nodes/polymarket/*`
- `app/data/nodes/nba/*`
- `app/data/nodes/hoopsstats/*`

If source endpoint is unstable/unavailable:
- implement fallback collector to persist streamed ticks/events,
- store to history tables for future analysis,
- mark source as `fallback_stream` in payload metadata.

### Checkpoint discipline
- each checkpoint file has status (`planned`, `in_progress`, `blocked`, `done`)
- each checkpoint file has explicit exit criteria
- do not skip checkpoint numbers
- do not delete prior checkpoint files

## Definition of done for each checkpoint
A checkpoint is only `done` when:
1. all in-scope implementation tasks are complete,
2. all required tests passed,
3. artifacts listed in checkpoint are updated,
4. evidence is written in checkpoint result section,
5. next checkpoint dependency gate is satisfied.

## Session handoff template
Use this template at end of work session and store in active checkpoint:
- `Date:`
- `Phase:`
- `What changed:`
- `Tests run:`
- `Pass/fail:`
- `Open blockers:`
- `Next exact action:`

## Collaboration Rule
- Ask the user whenever an external decision/input is required.
- Notify the user on each subphase completion (`v0.X.Y`) with:
  - what was completed,
  - what changed in schema/routes/planning,
  - what is next.

## Non-negotiable consistency checks
Before closing any session:
1. active checkpoint status is accurate
2. schema doc table-phase-columns relation reflects latest changes
3. route doc activation matrix reflects latest endpoint state
4. if phase completed, next phase file is switched to `in_progress`

## Fast reference commands (to keep workflow repeatable)
- list checkpoints: `Get-ChildItem dev-checkpoint`
- open active phase: `Get-Content -Raw dev-checkpoint/v0.3.4.md`
- list node files: `rg --files app/data/nodes`
- track schema mentions: `rg -n "activate:|table|column|phase" app/docs/scalable_db_schema_proposal.md`
- track routes mentions: `rg -n "v0\.|/v1/|Required tables" app/docs/scalable_api_routes_proposal.md`
- apply migrations: `python -m app.data.databases.migrate`

## Governance note
If work requested by user conflicts with phase ordering, document the exception in checkpoint and explicitly record dependency risks.

