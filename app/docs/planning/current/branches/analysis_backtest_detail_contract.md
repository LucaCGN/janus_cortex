# Branch Plan: `codex/analysis-backtest-detail-contract`

## Role
Read-only analysis contract branch for detailed per-family backtest comparison outputs.

## Target Milestone
- post-`v1.2.0` downstream contract handoff

## Depends On
- `codex/analysis-sampling-benchmarking`
- `codex/analysis-a8-consumer-adapters`

## Owns
- read-only per-family benchmark detail helpers
- normalized access to family summary, best/worst trades, context summaries, and trade traces
- analysis tests and reference docs for the comparison-detail contract

Likely write scope:
- `app/data/pipelines/daily/nba/analysis/consumer_adapters.py`
- analysis contract tests
- comparison-contract reference docs

## Does Not Own
- frontend layout or operator UX
- backtest engine math changes
- raw ingest or schema work

## Subphases

### `D1` Contract Shape
Objective:
- define the stable list and detail payloads for per-family comparison reads

Deliverables:
- canonical family index payload
- canonical per-family detail payload
- artifact-path naming rules and bounds for previews

Validation:
- the payloads are deterministic for the same versioned output bundle

### `D2` Consumer Helper Surface
Objective:
- expose the detail contract through reusable analysis helpers

Deliverables:
- family index loader
- family detail loader
- validation around missing or mismatched artifact bundles

Validation:
- downstream callers can load family detail without parsing benchmark artifacts manually

### `D3` Contract Tests And Handoff
Objective:
- freeze the read-only comparison-detail substrate for frontend consumption

Deliverables:
- fixture-backed tests for family summary, best/worst trades, context summaries, and traces
- reference doc updates
- explicit handoff note for the stacked frontend comparison branch

Validation:
- the detail contract passes unit tests
- the contract is ready for a follow-on frontend branch

## Merge Gate
- per-family comparison detail is available through one stable read-only contract
- no new benchmark computation or schema changes were introduced
- a stacked frontend comparison branch can consume the contract directly

## Handoff
Next branch:
- stacked frontend comparison branch on top of `codex/frontend-analysis-studio`
