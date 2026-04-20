# Branch Plan: `codex/analysis-sampling-benchmarking`

## Role
Critical-path benchmarking branch that turns the strategy lab into a disciplined experiment program.

## Target Milestone
- `v1.1.0`

## Depends On
- `codex/ops-analysis-validation`
- `codex/analysis-strategy-lab`

## Owns
- benchmark contract
- random holdout policy
- naive comparators
- experiment registry and summary artifacts

Likely write scope:
- backtest comparison utilities
- model comparison helpers if needed
- experiment-report docs and outputs

## Does Not Own
- net-new data ingestion
- frontend module work
- live strategy execution

## Subphases

### `B1` Benchmark Contract
Objective:
- define one comparison schema for all strategy families

Deliverables:
- canonical metrics list
- trade-count minimums
- slippage comparison requirements
- no-trade baseline and simple winner-prediction comparator definitions

Validation:
- every family can be scored against the same benchmark contract

### `B2` Random Holdout Framework
Objective:
- add the required 5%-10% random holdout alongside time-based validation

Deliverables:
- holdout generation rule
- reproducible seed or registry rule
- stratification guidance by date or opening band where necessary

Validation:
- repeated runs are reproducible
- holdout split does not leak information into benchmark results

### `B3` Experiment Registry
Objective:
- keep every strategy run comparable and traceable

Deliverables:
- experiment metadata output
- version, rule-set, slippage, sample, and date-range capture
- artifact index

Validation:
- an agent can compare two strategy runs without reading raw logs manually

### `B4` Comparative Reporting
Objective:
- produce side-by-side strategy comparison outputs

Deliverables:
- gross and slippage-adjusted return tables
- hold-time and MFE/MAE comparison views
- best and worst context buckets by strategy
- sample-versus-full-run comparison

Validation:
- strategy ranking can be explained with both metrics and context

### `B5` `v1.1.0` Backtest Candidate Freeze
Objective:
- define the first benchmarked multi-algo release candidate

Deliverables:
- frozen strategy set
- benchmark summary
- explicit keep, drop, and experimental labels

Validation:
- at least five strategy families have comparable benchmark outputs
- release candidate is reproducible on non-live DB

## Merge Gate
- benchmark contract is stable
- random holdout exists
- several strategies are compared under one registry and report surface

## Handoff
Next branches:
- `codex/analysis-a8-consumer-adapters`
- `codex/frontend-analysis-studio`
