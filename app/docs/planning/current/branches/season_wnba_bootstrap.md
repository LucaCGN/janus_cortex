# Branch Plan: `codex/season-wnba-bootstrap`

## Role
Secondary offseason branch for keeping basketball research active through WNBA preparation.

## Target Milestone
- `v1.4.x`

## Depends On
- `codex/data-dev-db-safety`

## Owns
- WNBA coverage audit
- schema and ingestion bootstrap planning
- offseason continuity plan that keeps the research program active

## Does Not Own
- current regular-season NBA benchmark critical path
- frontend work
- immediate algorithm benchmarking delivery

## Subphases

### `W1` Source Coverage Audit
Objective:
- measure what WNBA data is already fetchable historically and what is available going forward

### `W2` Schema And Canonical Planning
Objective:
- define how WNBA fits into the provider, canonical, and module structure

### `W3` Ingestion Baseline
Objective:
- create the first safe ingestion and persistence plan without destabilizing NBA flows

### `W4` Analysis Reuse Audit
Objective:
- decide which NBA analysis products can be reused for WNBA and which need separate logic

### `W5` Offseason Research Program
Objective:
- define how WNBA work keeps the broader basketball analysis effort productive during the NBA offseason

## Merge Gate
- WNBA path is documented and technically scoped
- offseason continuity no longer depends on ad hoc memory or branch-local notes
