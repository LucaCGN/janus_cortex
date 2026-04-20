# Branch Plan: `codex/frontend-analysis-comparison`

## Role
Follow-on frontend branch for family comparison views in the permanent analysis studio.

## Target Milestone
- `v1.3.1`

## Depends On
- `codex/frontend-analysis-studio`
- `codex/analysis-backtest-detail-contract`

## Owns
- family comparison views inside the permanent analysis studio
- read-only consumption of the per-family backtest detail contract
- frontend docs and router tests needed to expose comparison views safely

Likely write scope:
- `frontend/analysis_studio/*`
- `app/api/routers/analysis_studio.py`
- frontend-specific docs
- router-level tests for comparison reads

## Does Not Own
- backtest engine math
- report formatting
- raw ingest or DB migration logic
- the studio alpha scaffold already merged on `main`

## Subphases

### `F4a` Family Comparison Surface
Objective:
- add a read-only family index and family detail view for the already-merged backtest contract

Deliverables:
- family leaderboard/index panel
- family detail panel with bounded trade and context previews
- thin read-only routes to load comparison data from the consumer adapter layer

Validation:
- the same versioned bundle resolves deterministically
- unknown or missing families fail cleanly

### `F4b` Comparison UX Refinement
Objective:
- make the family comparison surface easier to scan and compare

Deliverables:
- selection controls for switching families
- clearer summary cards and detail grouping
- lightweight navigation from the strategy rankings table into comparison detail

Validation:
- comparison reads still stay read-only
- no new benchmark computation is introduced

## Merge Gate
- the permanent studio can show per-family comparison views without duplicating contract logic
- the branch remains read-only and does not change strategy math or report generation

## Handoff
Next dependent branches:
- `codex/season-playoffs-preseason`
- `codex/season-wnba-bootstrap`
