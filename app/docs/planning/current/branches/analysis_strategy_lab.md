# Branch Plan: `codex/analysis-strategy-lab`

## Role
Critical-path strategy expansion branch that turns the current offline substrate into a several-family algorithm lab.

## Target Milestone
- `v1.0.4`

## Depends On
- `codex/ops-analysis-validation`

## Owns
- strategy family interface and registry
- additional rule-based algorithm families
- trade-trace inspection artifacts
- strategy-level comparison inputs for the benchmarking branch

Likely write scope:
- `app/data/pipelines/daily/nba/analysis/backtests/*`
- related analysis tests
- analysis strategy docs

## Does Not Own
- database safety tooling
- benchmark sampling methodology
- frontend implementation
- season expansion work

## Subphases

### `S1` Strategy Interface Freeze
Objective:
- define the common contract that every strategy family must satisfy

Deliverables:
- strategy metadata contract
- shared inputs and outputs
- common artifact naming

Validation:
- all current baseline families still run through the shared interface

### `S2` Baseline Family Hardening
Objective:
- harden the existing three families into the new comparable interface

Families:
- favorite drawdown reversion
- underdog inversion continuation
- winner-definition continuation

Validation:
- output schema is stable across all three
- slippage-adjusted outputs remain available

### `S3` New Strategy Families
Objective:
- add at least two more distinct strategy families

Target additions:
- quarter-context comeback reversion
- opening-band volatility scalp

Optional third addition if evidence supports it:
- scoreboard-control mismatch fade or continuation

Validation:
- at least five total families can run
- each family is meaningfully distinct in trigger and exit logic

### `S4` Trade Trace And Visual Debug Outputs
Objective:
- make strategies inspectable rather than only aggregate-score visible

Deliverables:
- best-trade and worst-trade slices
- representative trade traces by context bucket
- artifact outputs suitable for later frontend consumption

Validation:
- operators can inspect why a trade won or lost

### `S5` Promotion To Benchmark Set
Objective:
- freeze the candidate strategy set that will enter benchmarking

Deliverables:
- benchmark-ready strategy registry
- list of dropped or experimental families
- handoff contract for sampling and benchmarking

Validation:
- several-family set exists and is stable enough for repeated comparison runs

## Merge Gate
- at least five strategy families runnable
- current and new families share one comparable contract
- visual trade-review artifacts exist

## Handoff
Next branch:
- `codex/analysis-sampling-benchmarking`
