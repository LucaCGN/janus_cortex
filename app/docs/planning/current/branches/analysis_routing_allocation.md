# Branch Plan: `codex/analysis-routing-allocation`

## Role
Critical-path analysis branch for deterministic portfolio routing, overlap control, and family-priority logic on top of the frozen `v1.4.2` strategy set.

## Target Milestone
- `v1.5.0`

## Depends On
- frozen `v1.4.2` benchmark state on `main`
- keep-family set:
  - `inversion`
  - `winner_definition`
  - `underdog_liftoff`

## Owns
- routed-sleeve robustness, not just single-family robustness
- priority-stack logic across keep families
- same-game handoff or state-machine rules if they remain deterministic
- overlap-cost diagnostics
- allocation and routing reference-doc updates

## Does Not Own
- new raw data products
- LLM gating
- frontend rendering
- schema or ingest changes
- team-specific hard-coded rules

## Subphases

### `R1` Freeze Portfolio Baselines
Objective:
- treat the current single-family and routed outputs as the comparison floor

Deliverables:
- frozen baseline table for:
  - `inversion`
  - `winner_definition`
  - `underdog_liftoff`
  - `combined_keep_families`
  - `statistical_routing_v1`

### `R2` Add Better Deterministic Routers
Objective:
- test deterministic routers that are richer than opening band only

Candidate router classes:
- opening band plus score-state bucket
- opening band plus period group
- family-priority stack
- same-game handoff from `inversion` to `winner_definition`

### `R3` Add Portfolio Diagnostics
Objective:
- explain why sleeves underperform or beat single-family lanes

Deliverables:
- overlap-cost summary
- family-block count
- skipped-positive-trade summary
- routed-sleeve robustness

### `R4` Freeze The Allocation Surface
Objective:
- publish one promoted deterministic allocation baseline

Deliverables:
- promoted routed or priority portfolio
- explicit rejected router variants
- updated planning and reference docs

## Merge Gate
- focused pytest sweep passes
- real benchmark run emits the promoted routed/allocation artifacts
- promoted router improves robustness or drawdown-adjusted portfolio quality, not just full-sample ending bankroll

## Handoff
Next branch:
- `codex/analysis-context-models`
- or `codex/frontend-analysis-portfolio-viz` once the promoted allocation surface is frozen
