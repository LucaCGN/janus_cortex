# NBA Analysis Next Steps

## End Goal
Turn the current offline NBA analysis stack into a repeatable trading-research program that can:
- measure human-expectation drift and intragame odds volatility
- simulate strategies as a sequential bankroll path instead of only as independent wins and losses
- validate profitability against naive baselines, random holdouts, and slippage stress
- refine strategy families under the sequential capital framing before promoting any candidate
- carry the program forward into playoffs, preseason, and WNBA continuity work

Detailed milestone and branch decomposition now lives in:
- [roadmap_to_multi_algo_backtests.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/roadmap_to_multi_algo_backtests.md)
- [branches/README.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/README.md)

## What Is Already Done
- `A0-A7` implementation wave completed:
  - package split
  - research universe and QA layer
  - mart builders
  - descriptive report pack
  - backtest engine
  - predictive baselines
  - player-impact shadow lane
- `v1.3.1` family comparison frontend is merged to `main`
- current CLI surface present:
  - `build_analysis_mart`
  - `build_analysis_report`
  - `run_analysis_backtests`
  - `train_analysis_baselines`

## Validation Status
- the offline structure is in place
- disposable non-live validation has already been used for the merged analysis lanes
- strategy refinement now needs to be judged with sequential bankroll accounting, not just raw win-rate snapshots

## Current Gaps
- sequential bankroll accounting is not yet the primary evaluation lens
- the surviving strategy families still need to be rerun under the new linear capital progression
- repeated-resample robustness for the sequential framing still needs to be added
- deeper model diagnostics remain a follow-on lane
- playoffs, preseason, and WNBA continuity work are still not in the active lane

## Next Phase Order

### 1. Sequential Portfolio Benchmarking
Branches:
- `codex/analysis-sequential-portfolio-benchmarking`

Goals:
- freeze the bankroll rules
- rerun each strategy family independently as a sequential capital path
- compare ending bankroll, drawdown, and capital-at-risk on random-holdout and naive baselines

Exit criteria:
- each family produces a deterministic sequential portfolio result
- the baseline and holdout comparisons are reproducible
- the strategy ranking is explained in portfolio terms, not just per-trade terms

### 2. Strategy Refinement
Branches:
- `codex/analysis-sequential-portfolio-benchmarking`

Goals:
- tighten the strategy families that survive sequential capital accounting
- identify which triggers or exits should be dropped, narrowed, or split into separate experimental variants
- keep the family comparisons independent before any portfolio blend is considered

Exit criteria:
- refined families are explicitly documented
- the portfolio simulation is still deterministic after refinement
- any strategy that fails the new premise is marked for drop or experimental handling

### 3. Season Continuity
Branches:
- `codex/season-playoffs-preseason`
- `codex/season-wnba-bootstrap`

Goals:
- keep the data platform alive beyond the closed regular season
- prepare playoff, preseason, and WNBA structures without breaking the regular-season analysis lane

Exit criteria:
- season-scope schemas and sync logic are in place
- offseason work can continue on basketball data rather than waiting for the next NBA regular season

## Product Questions To Keep Answering
- when is a game statistically defined, and which comeback classes still break that rule?
- which strategy families actually compound capital when the bankroll is carried forward?
- how much drawdown can each family tolerate before the sequential path breaks?
- which odds paths are driven by repeatable context versus isolated narratives?
- which player-presence or play-type effects are strong enough to matter without over-claiming causality?
