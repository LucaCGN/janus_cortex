# Branch Plan: `codex/season-playoffs-preseason`

## Role
Secondary season-continuity branch for keeping the data platform alive beyond the regular season.

## Target Milestone
- `v1.4.x`

## Depends On
- `codex/data-dev-db-safety`

## Owns
- play-in, playoffs, and preseason season-scope planning and support
- schema and pipeline preparation needed for next basketball phases

## Does Not Own
- the critical path to `v1.1.0`
- frontend work
- regular-season strategy benchmarking logic

## Subphases

### `P1` Season-Scope Audit
Objective:
- inventory what breaks when moving from regular season to play-in, playoffs, and preseason

### `P2` Schema And Contract Preparation
Objective:
- define missing season-scope tables, flags, and mapping rules

### `P3` Pipeline Readiness
Objective:
- ensure daily sync and recovery flows can receive non-regular-season games safely

### `P4` Analysis Compatibility Decision
Objective:
- decide which regular-season analysis assumptions remain valid and which become separate lanes

### `P5` Handoff And Documentation
Objective:
- document what is ready now versus what remains blocked for postseason work

## Merge Gate
- season-continuity support no longer depends on undocumented assumptions
- regular-season analysis branch remains unblocked and clean
