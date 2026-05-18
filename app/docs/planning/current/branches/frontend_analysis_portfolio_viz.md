# Branch Plan: `codex/frontend-analysis-portfolio-viz`

Status: superseded 2026-05-17

> Superseded by the backend-first final-system direction. The tracked `frontend/` module was removed; future visualization/UI work requires a new GitHub issue, source-of-truth update, and operator approval.

## Role
Historical read-only visualization lane for the frozen benchmark, robustness, and controller outputs exposed through the previous analysis studio surface.

## Target Milestone
- `v1.5.2`

## Depends On
- `codex/analysis-routing-allocation`
- stable consumer adapters and existing studio routes already on `main`

## Owns
- `app/data/pipelines/daily/nba/analysis/consumer_adapters.py`
- read-only screens for portfolio rankings
- robustness tables and distributions
- master-router comparison and selection diagnostics
- route maps and opening-band diagnostics
- family comparison views for the promoted keep set

## Does Not Own
- live order entry
- strategy math changes
- DB migrations
- ad hoc artifact generation in the repo root

## Subphases

### `F1` Portfolio Surface
Deliverables:
- individual-strategy ranking with bankroll, robustness, and drawdown columns
- full-sample portfolio-lane ranking
- holdout and robustness summaries for the controller and baseline lanes
- clear distinction between single-family, combined, and routed lanes

### `F2` Family Drilldowns
Deliverables:
- trade-path tables
- context summaries
- best and worst trade samples

### `F3` Router Diagnostics
Deliverables:
- core-family composition for the master router
- opening-band route map versus master-router selection counts
- overlap-friction tables and blocked-trade counts by family

## Merge Gate
- read-only routes work against the consumer adapter layer
- no benchmark math is reimplemented in any future visualization surface
- the UI reflects frozen artifact contracts rather than bespoke transforms
- the UI names the actual promoted building blocks and master-router family from the benchmark bundle

## Handoff
Next branch:
- later UX polish
- or season-continuity visualization once those data lanes are ready
