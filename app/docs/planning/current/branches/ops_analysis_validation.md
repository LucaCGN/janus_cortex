# Branch Plan: `codex/ops-analysis-validation`

## Role
Critical-path validation branch that proves the current offline analysis stack works on a non-live database.

## Target Milestone
- `v1.0.3`

## Depends On
- `codex/data-dev-db-safety`

## Owns
- full offline validation checklist
- non-live mart, report, backtest, and model execution proof
- validation result capture and artifact summary

Likely write scope:
- validation docs or scripts under `tools/`
- analysis validation runbooks
- possibly minor compatibility or bug fixes exposed by validation

## Does Not Own
- net-new strategy families
- frontend module work
- playoff or WNBA expansion

## Subphases

### `O1` Validation Checklist Freeze
Objective:
- define the exact run order for mart, report, backtest, and model validation on non-live DB

Deliverables:
- command checklist
- expected outputs and artifact locations
- failure triage order

Validation:
- checklist can be run start to finish without ambiguous steps

### `O2` Corpus Reconciliation
Objective:
- verify the current research universe, mart counts, and QA residuals on the non-live DB

Deliverables:
- counts for finished games, research-ready games, descriptive-only games, and residual coverage classes
- note on any drift from previous baselines

Validation:
- universe counts match the current documented baseline or drift is explained explicitly

### `O3` Full Offline Command Sweep
Objective:
- run the core analysis commands on non-live DB

Deliverables:
- mart build result
- report build result
- backtest smoke result
- model smoke result

Validation:
- commands complete or fail with explicit, documented blockers

### `O4` Bottleneck And Reliability Review
Objective:
- identify runtime, data-quality, or operational issues before strategy expansion

Deliverables:
- list of blockers
- list of non-blocking rough edges
- recommendation on whether the critical path can continue

Validation:
- blocking issues are either fixed or handed forward with explicit ownership

### `O5` Validation Summary And Handoff
Objective:
- publish the validated baseline that strategy branches will rely on

Deliverables:
- validated baseline summary
- artifact list
- handoff requirements for `codex/analysis-strategy-lab` and `codex/analysis-sampling-benchmarking`

Validation:
- the next branch can trust the offline substrate without re-proving the entire current stack

## Merge Gate
- full non-live validation checklist completed
- resulting issues triaged
- validated baseline documented

## Handoff
Next branches:
- `codex/analysis-strategy-lab`
- `codex/analysis-sampling-benchmarking`
