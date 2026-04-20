# Roadmap To Multi-Algorithm Backtests

## Purpose
Define the critical path from the current `v1_0_1` offline analysis baseline to the first release where several different trading algorithms can be backtested, compared, and visually reviewed on the same research substrate.

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
| `v1.4.x` | season continuity expansion | `codex/season-playoffs-preseason`, `codex/season-wnba-bootstrap` |

Planning note:
- these are planning milestones for the current execution wave
- they do not replace schema migration naming or historic `v0.x` implementation history

## Critical Path To `v1.1.0`
1. database safety and validation foundation
2. non-live proof that current mart, report, backtest, and model commands are stable
3. expansion from three baseline strategy families to a several-family strategy lab
4. benchmarking framework with random holdouts, naive comparators, and experiment registry outputs

## What Counts As "Several Different Algos"
The target is at least five distinct strategy families runnable through one comparable backtest workflow:
- favorite drawdown reversion
- underdog inversion continuation
- winner-definition continuation
- quarter-context comeback reversion
- opening-band volatility scalp

Optional sixth family if evidence supports it:
- scoreboard-control mismatch fade or continuation

## `v1.1.0` Exit Criteria
- at least five strategy families can run from the same CLI surface or shared experiment interface
- every family emits:
  - per-trade logs
  - slippage-adjusted results
  - MFE and MAE statistics
  - hold-time distributions
  - context tags
- every family is evaluated against:
  - time-based validation
  - 5%-10% random holdout sample
  - naive no-trade and naive winner-prediction style comparators where relevant
- visual and tabular feedback exists for the best and worst trades
- the entire evaluation run is reproducible on a non-live database

## Branch Dependency Graph

### Critical Path
1. [branches/data_dev_db_safety.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/data_dev_db_safety.md)
2. [branches/ops_analysis_validation.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/ops_analysis_validation.md)
3. [branches/analysis_strategy_lab.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/analysis_strategy_lab.md)
4. [branches/analysis_sampling_benchmarking.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/analysis_sampling_benchmarking.md)

### Downstream After `v1.1.0`
5. [branches/analysis_consumer_adapters.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/analysis_consumer_adapters.md)
6. [branches/frontend_analysis_studio.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/frontend_analysis_studio.md)

### Parallel Or Secondary Tracks
- [branches/season_playoffs_preseason.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/season_playoffs_preseason.md)
- [branches/season_wnba_bootstrap.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/season_wnba_bootstrap.md)

## Stable Risks
- running migration or validation work against the live database too early
- promoting strategies without slippage and holdout checks
- letting frontend work begin before read-only contracts stabilize
- mixing seasonal continuity work into the critical path before the regular-season analysis program is benchmark-ready
