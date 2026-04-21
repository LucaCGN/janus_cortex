# Branch Plan: `codex/analysis-context-models`

## Role
Analysis branch for the first statistical models built around the promoted deterministic families, with emphasis on context scoring rather than unconstrained prediction.

## Target Milestone
- `v1.5.1`

## Depends On
- `codex/analysis-routing-allocation` for the promoted deterministic allocation baseline
- frozen `v1.4.2` strategy rules and benchmark artifacts

## Owns
- model-ready target definitions for the winning families
- context-score baselines for routing or trade quality
- residual-dislocation prototypes that compare market price with a simple state-based fair-value estimate
- documentation of which models are promotion-grade versus research-only

## Does Not Own
- live execution
- free-form LLM routing
- frontend charting
- player-level enrichment beyond what already exists in the mart

## Subphases

### `M1` Define Model Targets
Targets to evaluate:
- `inversion` continuation quality after the trigger
- `winner_definition` persistence or reopen risk after `80c`
- `underdog_liftoff` target-hit versus stop-hit probability
- portfolio-route score by opening band, period, and score context

### `M2` Train Simple Statistical Baselines
Allowed baseline families:
- logistic regression
- regularized linear models
- simple tree-based ranking only if feature counts stay small and diagnostics stay interpretable

### `M3` Score Against The Deterministic Baseline
Deliverables:
- holdout metrics
- calibration or ranking diagnostics
- side-by-side comparison with deterministic routing

### `M4` Freeze Model Boundaries
Objective:
- decide what remains purely deterministic and where a later interpretive LLM layer could add structured tags without becoming the primary predictor

## Merge Gate
- model artifacts are reproducible
- benchmarks stay paired against the same candidate universe
- no model is promoted without outperforming a deterministic baseline on holdout and robustness-aware diagnostics

## Handoff
Next branch:
- `codex/frontend-analysis-portfolio-viz`
- or a later structured-tag lane if statistical baselines expose a real gap that text context can plausibly fill
