# Branch Plan: `codex/data-dev-db-safety`

## Role
Critical-path foundation branch for safe database work.

## Target Milestone
- `v1.0.2`

## Depends On
- none

## Owns
- DB validation runbooks
- disposable Postgres workflow
- dev-clone safety workflow
- migration safety instructions and related tooling or docs

Likely write scope:
- `app/data/databases/*`
- `tests/app/data/databases/*`
- `app/docs/development_guide.md`
- `app/docs/local_workspace_convention.md`
- DB safety docs or scripts under `tools/`

## Does Not Own
- strategy algorithm design
- report logic
- frontend scaffolding
- season expansion logic

## Subphases

### `D1` Environment Boundary Inventory
Objective:
- document which env vars and commands hit local, disposable, dev-clone, or live Postgres

Deliverables:
- explicit boundary map for DB targets
- naming convention for local disposable DB versus dev clone versus live DB
- checklist that prevents accidental live DB writes

Validation:
- commands are executable and clearly separated by environment

### `D2` Disposable Postgres Bootstrap
Objective:
- create a reproducible local Postgres workflow built from migrations only

Deliverables:
- bootstrap command or script
- reset command or script
- minimal smoke validation for schema migration and teardown

Validation:
- fresh disposable DB can be created and migrated from zero
- teardown does not affect any shared DB

### `D3` Migration Safety Harness
Objective:
- standardize how migrations are validated before any shared-db usage

Deliverables:
- migration smoke checklist
- required pytest commands
- rollback or rebuild guidance for disposable environments

Validation:
- migration inventory and migration test commands pass on disposable Postgres

### `D4` Dev-Clone Workflow
Objective:
- define the path from disposable validation to realistic validation on a dev copy of live data

Deliverables:
- dev-clone creation or restore runbook
- preflight checklist before applying any migration or heavy analysis job
- note on data safety and restore expectations

Validation:
- the runbook is specific enough that an agent can follow it without improvisation

### `D5` Merge Gate And Handoff
Objective:
- freeze the DB safety ladder before any further critical-path branch work

Deliverables:
- final runbook summary
- updated agent rules
- handoff list for `codex/ops-analysis-validation`

Validation:
- documentation and scripts are enough to keep `JANUS_RUN_DB_TESTS=1` off live DB by default

## Merge Gate
- disposable Postgres workflow documented and tested
- migration safety ladder documented
- dev-clone validation workflow documented
- next branch can run full analysis validation without inventing DB process

## Handoff
Next branch:
- `codex/ops-analysis-validation`
