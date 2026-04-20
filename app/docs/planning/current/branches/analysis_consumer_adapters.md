# Branch Plan: `codex/analysis-a8-consumer-adapters`

## Role
Downstream branch that freezes read-only contracts for consumers after the several-algorithm benchmark set is stable.

## Target Milestone
- `v1.2.0`

## Depends On
- `codex/analysis-sampling-benchmarking`

## Owns
- read-only adapter contracts
- version-aware artifact and mart loading
- stable summary surfaces for frontend or later LLM usage

Likely write scope:
- adapter layer under backend analysis-serving modules
- adapter tests
- consumer contract docs

## Does Not Own
- raw ingest pipelines
- strategy family math
- frontend rendering

## Subphases

### `C1` Contract Inventory
Objective:
- list which report, benchmark, and model outputs need stable downstream access

### `C2` Adapter Surface
Objective:
- build read-only loaders and summary builders

### `C3` Version Resolution
Objective:
- define how consumers pick analysis version, experiment run, and artifact paths safely

### `C4` Contract Tests
Objective:
- prove consumers can read stable outputs without touching raw ingest tables

### `C5` Frontend Handoff
Objective:
- publish the contract the frontend module will consume

## Merge Gate
- adapter contracts stable
- version resolution defined
- frontend branch no longer needs to guess output structure
