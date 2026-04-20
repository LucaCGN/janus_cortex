# Roadmap To Multi-Algorithm Portfolio Validation

## Purpose
Define the critical path from the current `v1_0_1` offline analysis baseline to the first release where several different trading algorithms can be replayed, stress-tested across repeated random seeds, combined into sleeves, and eventually allocated under one comparable capital program.

## Milestone Ladder

| Milestone | Meaning | Critical Branches |
| --- | --- | --- |
| `v1.0.1` | current offline analysis baseline | already completed |
| `v1.0.2` | safe database validation foundation | `codex/data-dev-db-safety` |
| `v1.0.3` | full non-live validation of current offline stack | `codex/ops-analysis-validation` |
| `v1.0.4` | expanded strategy-lab release with multiple runnable strategy families | `codex/analysis-strategy-lab` |
| `v1.1.0` | benchmarked multi-algorithm backtest candidate | `codex/analysis-sampling-benchmarking` plus `codex/analysis-strategy-lab` |
| `v1.2.0` | stable read-only consumer adapters | `codex/analysis-a8-consumer-adapters` |
| `v1.3.0` | frontend analysis studio alpha | `codex/frontend-analysis-studio` |
| `v1.3.1` | read-only family comparison follow-on lane | `codex/frontend-analysis-comparison` |
| `v1.4.0` | sequential portfolio benchmark | `codex/analysis-sequential-portfolio-benchmarking` |
| `v1.4.1` | repeated-seed robustness and combined keep-family sleeve | `codex/analysis-portfolio-robustness` |
| `v1.4.2` | allocation / visualization follow-on | read-only visualization lane or combined-sleeve allocation lane |
| `v1.4.x` | season continuity expansion | `codex/season-playoffs-preseason`, `codex/season-wnba-bootstrap` |

Planning note:
- these are planning milestones for the current execution wave
- they do not replace schema migration naming or historic `v0.x` implementation history
- the earlier multi-algorithm benchmark work is complete
- this roadmap now governs the next layer: sequential bankroll simulation, robustness checks, and sleeve construction

## Critical Path To Portfolio Validation
1. freeze the sequential bankroll accounting contract
2. replay each candidate family as a linear opportunity progression
3. compare ending bankroll, drawdown, and capital-at-risk behavior against random-holdout and naive baselines
4. confirm the surviving families across repeated holdout seeds
5. construct the first combined keep-family sleeve
6. decide whether the next lane is allocation logic or visualization

## What Counts As "Sequential Portfolio Ready"
The target is a set of strategy families that can be rerun through one comparable bankroll simulation workflow:
- favorite drawdown reversion
- underdog inversion continuation
- winner-definition continuation
- quarter-context comeback reversion
- opening-band volatility scalp

Optional sixth family if evidence supports it:
- scoreboard-control mismatch fade or continuation

## `v1.4.x` Exit Criteria
- each family can be replayed independently under the same bankroll contract
- the simulation records ending capital, drawdown, and trade-by-trade capital usage
- the sequential path is deterministic for the same inputs and seed
- the evaluation is reproducible on a non-live database
- comparative outputs exist for random-holdout and naive baselines
- refined and dropped strategy candidates are documented explicitly
- surviving families are checked across repeated seeds
- the first combined keep-family sleeve is emitted and documented

## Branch Dependency Graph

### Critical Path
1. [branches/analysis_sequential_portfolio_benchmarking.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/analysis_sequential_portfolio_benchmarking.md)
2. [branches/analysis_portfolio_robustness.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/analysis_portfolio_robustness.md)

### Parallel Or Secondary Tracks
- [branches/season_playoffs_preseason.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/season_playoffs_preseason.md)
- [branches/season_wnba_bootstrap.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/season_wnba_bootstrap.md)

## Stable Risks
- running migration or validation work against the live database too early
- promoting strategies without explicit sequential bankroll and holdout checks
- letting seasonal continuity work leak into the sequential portfolio lane
- changing strategy trigger logic and bankroll accounting in the same branch
