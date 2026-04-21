# Roadmap To Multi-Algorithm Portfolio Validation

## Purpose
Define the critical path from the current `v1_0_1` offline analysis baseline to the first release where several different trading algorithms are:
- benchmarked under one comparable bankroll contract
- routed against each other deterministically
- stress-tested across repeated seeds
- compared against lightweight statistical context models

## Milestone Ladder

| Milestone | Meaning | Status | Critical Branches |
| --- | --- | --- | --- |
| `v1.0.1` | offline analysis baseline | completed | already merged |
| `v1.0.2` | safe database validation foundation | completed | archived |
| `v1.0.3` | full non-live validation of current offline stack | completed | archived |
| `v1.0.4` | expanded strategy-lab release | completed | archived |
| `v1.1.0` | benchmarked multi-algorithm backtest candidate | completed | archived |
| `v1.2.0` | stable read-only consumer adapters | completed | archived |
| `v1.3.0` | frontend analysis studio alpha | completed | archived |
| `v1.3.1` | read-only family comparison follow-on lane | completed | archived |
| `v1.4.0` | sequential portfolio benchmark | completed | archived |
| `v1.4.1` | repeated-seed robustness and combined keep-family sleeve | completed | archived |
| `v1.4.2` | final strategy refinement and first statistical routing lane | completed | now on active branch state |
| `v1.4.3` | realistic execution replay, quarter-specific families, and richer benchmark artifacts | completed | current frozen baseline |
| `v1.5.0` | deterministic routing and allocation freeze | next critical path | `codex/analysis-routing-allocation` |
| `v1.5.1` | context-model baselines around promoted families | next after routing baseline | `codex/analysis-context-models` |
| `v1.5.2` | read-only portfolio visualization | parallel after routing freeze | `codex/frontend-analysis-portfolio-viz` |
| `v1.5.x` | season continuity expansion | secondary sidecars | `codex/season-playoffs-preseason`, `codex/season-wnba-bootstrap` |

## Current Promotion Baseline
The promoted deterministic family set is now:
- `inversion`
- `winner_definition`
- `underdog_liftoff`
- `q1_repricing`
- `q4_clutch`

The current rejected family set is:
- `reversion`
- `comeback_reversion`
- `volatility_scalp`

## Critical Path From Here
1. freeze a better deterministic routed or priority portfolio
2. quantify overlap cost, family blocking, and actual concurrent-position pressure under the `v7` replay contract
3. turn the new per-game strategy-classification artifact into a deterministic routing or ranking control
4. build statistical context models against that deterministic control
5. surface the resulting portfolio and robustness outputs in read-only UI

## What Counts As "Several Backtestable Algos"
The target is no longer just several independent family rules. The target is:
- multiple standalone families under the same bankroll contract
- at least one promoted routed or priority portfolio
- model-ready targets for continuation, persistence, or route-quality scoring

## Branch Dependency Graph

### Critical Path
1. [branches/analysis_routing_allocation.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/analysis_routing_allocation.md)
2. [branches/analysis_context_models.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/analysis_context_models.md)

### Parallel Or Secondary Tracks
- [branches/frontend_analysis_portfolio_viz.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/frontend_analysis_portfolio_viz.md)
- [branches/season_playoffs_preseason.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/season_playoffs_preseason.md)
- [branches/season_wnba_bootstrap.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/season_wnba_bootstrap.md)

## Branch Dependency Table

| Branch | Milestone | Depends On | Can Run In Parallel With | Blocks | Notes |
| --- | --- | --- | --- | --- | --- |
| `codex/analysis-routing-allocation` | `v1.5.0` | frozen `v1.4.3` keep families and `v7` replay contract | season branches | context models and final portfolio visualization | next critical branch |
| `codex/analysis-context-models` | `v1.5.1` | `codex/analysis-routing-allocation` | frontend visualization prep, season branches | later structured-tag or richer modeling work | model work needs a deterministic control |
| `codex/frontend-analysis-portfolio-viz` | `v1.5.2` | `codex/analysis-routing-allocation` | `codex/analysis-context-models`, season branches | no critical-path branch | read-only consumer lane |
| `codex/season-playoffs-preseason` | `v1.5.x` | safety workflow already merged | routing/allocation, context models, WNBA | no critical-path branch | secondary lane |
| `codex/season-wnba-bootstrap` | `v1.5.x` | safety workflow already merged | routing/allocation, context models, playoffs | no critical-path branch | secondary lane |

## Stable Risks
- optimizing sleeve rules on the same branch that changes raw family math
- letting visualization reimplement benchmark logic instead of reading artifacts
- promoting model lanes before deterministic routing is frozen
- using text-driven heuristics before the structured statistical control is strong enough
