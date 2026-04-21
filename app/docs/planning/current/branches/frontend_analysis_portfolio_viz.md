# Branch Plan: `codex/frontend-analysis-portfolio-viz`

## Role
Read-only visualization branch for the frozen benchmark, robustness, and portfolio-routing outputs.

## Target Milestone
- `v1.5.2`

## Depends On
- `codex/analysis-routing-allocation`
- stable consumer adapters and existing studio routes already on `main`

## Owns
- read-only screens for portfolio rankings
- robustness tables and distributions
- route maps and overlap diagnostics
- family comparison views for the promoted keep set

## Does Not Own
- live order entry
- strategy math changes
- DB migrations
- ad hoc artifact generation in the repo root

## Subphases

### `F1` Portfolio Surface
Deliverables:
- full-sample portfolio ranking
- holdout and robustness summaries
- clear distinction between single-family, combined, and routed lanes

### `F2` Family Drilldowns
Deliverables:
- trade-path tables
- context summaries
- best and worst trade samples

### `F3` Router Diagnostics
Deliverables:
- opening-band route map
- overlap-friction tables
- blocked-trade counts by family

## Merge Gate
- read-only routes work against the consumer adapter layer
- no benchmark math is reimplemented in the frontend
- the UI reflects frozen artifact contracts rather than bespoke transforms

## Handoff
Next branch:
- later UX polish
- or season-continuity visualization once those data lanes are ready
