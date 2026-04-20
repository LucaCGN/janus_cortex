# Branch Plan: `codex/analysis-sequential-portfolio-benchmarking`

## Role
Analysis branch for sequential bankroll simulation and strategy refinement under portfolio-style capital progression.

## Target Milestone
- `v1.4.0`

## Depends On
- `codex/analysis-sampling-benchmarking`
- `codex/analysis-backtest-detail-contract`
- `codex/analysis-a8-consumer-adapters`

## Owns
- sequential bankroll accounting rules
- strategy-by-strategy replay over a linear opportunity progression
- portfolio-style end-capital, drawdown, and capital-at-risk summaries
- refinement of the surviving strategy families under the new sequential premises
- random-holdout and naive-baseline comparison outputs for the sequential framing

## Current Status
- `P1` complete:
  - bankroll contract frozen at `10.0` starting capital, `1.0` position fraction, `100`-game window, one-open-position-at-a-time
- `P2` complete:
  - per-family portfolio replay is now emitted as benchmark artifacts
- `P3` complete for the current default family set:
  - `inversion` and `winner_definition` survive the sequential lens
  - `reversion`, `comeback_reversion`, and `volatility_scalp` are currently drop candidates
  - tested threshold-tightening variants for `inversion` and `winner_definition` were rejected because they underperformed the original defaults under bankroll compounding
- `P4` complete for the first seeded holdout pass:
  - sequential candidate-freeze outputs now include full, time-validation, and random-holdout bankroll results
- current focus inside `P5`:
  - publish the frozen results
  - decide whether the next branch should be repeated-seed robustness, combined portfolio construction, or visualization

Likely write scope:
- `app/data/pipelines/daily/nba/analysis/backtests/*`
- sequential benchmark helpers and tests
- analysis planning and reference docs

## Does Not Own
- frontend rendering
- raw ingest or schema migration work
- report formatting unrelated to portfolio simulation
- changing the underlying strategy trigger logic without a separate refinement slice

## Subphases

### `P1` Sequential Capital Contract
Objective:
- freeze the accounting rules for a linear bankroll path before any strategy reruns

Deliverables:
- explicit starting bankroll
- deterministic stake-sizing rule
- capital carry-forward rules
- documented treatment of wins, losses, and skipped opportunities

Validation:
- the same input bundle always produces the same capital path

### `P2` Strategy Replay Harness
Objective:
- rerun each candidate family independently against the sequential accounting contract

Deliverables:
- per-family replay output
- ending bankroll
- max drawdown and drawdown duration
- trade-count and capital-at-risk summaries

Validation:
- each family can be rerun in isolation with identical results for the same seed and inputs

### `P3` Strategy Refinement Under Sequential Rules
Objective:
- tighten the families that survive the sequential accounting test

Deliverables:
- narrowed trigger and exit rules
- candidate revisions or drops
- explicit notes on which families improve under sequential capital and which do not

Validation:
- the refined family set remains deterministic and comparable

### `P4` Random-Sample And Baseline Comparison
Objective:
- score the sequential portfolio runs against holdout and naive references

Deliverables:
- random-sample sequential portfolio summary
- no-trade and naive comparator summaries
- candidate freeze labels for sequential performance

Validation:
- the sequential benchmark is reproducible on a non-live database

### `P5` Handoff
Objective:
- publish the first sequential portfolio benchmark set and hand off any follow-on diagnostics work

Deliverables:
- frozen sequential portfolio benchmark summary
- archived experiment metadata
- clear next-step note for any later visualization or diagnostics branch

Validation:
- the branch can hand off cleanly without changing strategy math again

## Merge Gate
- the same strategy family can be evaluated as both a standalone family and a sequential bankroll path
- ending bankroll, drawdown, and capital-at-risk are documented for the candidate set
- the output is deterministic and comparable across the frozen strategy families

## Handoff
Next branch:
- follow-on diagnostics or visualization lane if the sequential outputs need a dedicated surface
- season-continuity branches can continue in parallel once the sequential lane is scoped
