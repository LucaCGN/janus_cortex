# Roadmap To Multi-Algorithm Portfolio Validation

## Purpose
Define the path from the offline NBA analysis baseline to the first stable controller release where:
- the underlying families are frozen
- execution friction is modeled aggressively enough to be informative
- deterministic and LLM-assisted controllers can be compared on the same corpus
- postseason validation exists outside the regular-season 100-game replay

## Milestone Ladder

| Milestone | Meaning | Status | Notes |
| --- | --- | --- | --- |
| `v1.0.1` | offline analysis baseline | completed | mart, report, and baseline research universe |
| `v1.0.2` | safe database validation foundation | completed | archived |
| `v1.0.3` | full non-live validation of current offline stack | completed | archived |
| `v1.0.4` | expanded strategy-lab release | completed | archived |
| `v1.1.0` | benchmarked multi-algorithm backtest candidate | completed | archived |
| `v1.2.0` | stable read-only consumer adapters | completed | archived |
| `v1.3.0` | frontend analysis studio alpha | completed | archived |
| `v1.3.1` | read-only family comparison follow-on lane | completed | archived |
| `v1.4.0` | sequential portfolio benchmark | completed | archived |
| `v1.4.1` | repeated-seed robustness and combined keep-family sleeve | completed | archived |
| `v1.4.2` | final strategy refinement and first statistical routing lane | completed | archived |
| `v1.4.3` | realistic execution replay and quarter-specific family work | completed | archived |
| `v1.4.4` | expanded family research and master-router baseline | completed | archived |
| `v1.4.5` | restrained LLM router benchmark and finalist showdown | completed | archived |
| `v1.4.6` | cross-model LLM router evaluation | completed | archived into current references |
| `v1.5.0` | hostile-execution postseason validation contract | completed | current frozen benchmark contract is `v11` |
| `v1.5.1` | controller hardening under hostile execution | next critical path | deterministic vs LLM controller tuning |
| `v1.5.2` | focused finalist dashboard and review queue | parallel after controller hardening | tune the four kept options only |
| `v1.5.3` | live-season continuity and playoffs refresh automation | secondary | data freshness / season rollover |

## Frozen Analysis Baseline

### Underlying Strategy Stack
These are the underlying families still used by the promoted controllers:
- core routed families:
  - `winner_definition`
  - `inversion`
  - `underdog_liftoff`
- independent trigger sleeves:
  - `q1_repricing`
  - `q4_clutch`

Everything else is now archived, rejected, or research-only. In particular:
- archived or demoted:
  - `favorite_panic_fade_v1`
  - `halftime_q3_repricing_v1`
  - `comeback_reversion_v2`
  - `model_residual_dislocation_v1`
- rejected:
  - `reversion`
  - `comeback_reversion`
  - `volatility_scalp`

### Final Compared Options
The final external comparison set is now frozen to:
- `winner_definition`
- `master_strategy_router_v1`
- `gpt-5.4 :: llm_hybrid_freedom_compact_v1`
- `gpt-5.4-mini :: llm_hybrid_freedom_compact_v1`

## Frozen Execution Contract
Current benchmark contract: `v11`

Current hostile-execution replay defaults:
- initial bankroll: `$10.00`
- position size fraction: `0.20`
- maximum concurrent positions: `5`
- concurrency mode: `shared_cash_equal_split`
- minimum order: `$1.00`
- minimum shares: `5`
- deterministic slippage: `0c`
- random adverse slippage: `0-20c`

Interpretation:
- this is no longer the optimistic 100-game regular-season benchmark
- this is a more realistic stress contract meant to test survival under overlap and poor fills

## Validated Postseason Corpus
The current hard validation set is a fixed `20`-game postseason corpus:
- `6` play-in games
- `14` playoff games

Validation status:
- all `20` games are research-ready
- all `20` have linked Polymarket history
- all `20` have play-by-play loaded
- all `20` are materialized into the analysis mart

Reference:
- [postseason_final_20_validation.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/reference/postseason_final_20_validation.md)

## Current Scoreboard
On the fixed postseason-final-20 replay under the `v11` hostile-execution contract:

| Option | Ending Bankroll | Compounded Return | Max Drawdown | Trades |
| --- | ---: | ---: | ---: | ---: |
| `master_strategy_router_v1` | `$3.97` | `-60.25%` | `60.25%` | `11` |
| `gpt-5.4-mini :: llm_hybrid_freedom_compact_v1` | `$3.71` | `-62.87%` | `67.78%` | `19` |
| `gpt-5.4 :: llm_hybrid_freedom_compact_v1` | `$3.62` | `-63.77%` | `63.77%` | `21` |
| `winner_definition` | `$2.68` | `-73.21%` | `73.21%` | `9` |

This is the current truth set for planning:
- the deterministic router preserves bankroll best on the hostile postseason slice
- the LLM lanes improve trade density but do not yet beat the deterministic router under this harsher contract
- the LLM is not yet promotable as the primary controller

## Critical Path From Here
1. harden `master_strategy_router_v1` under the `v11` hostile-execution contract
2. use the two LLM finalist lanes only as bounded challengers to the deterministic router
3. separate controller tuning from underlying family creation
4. tighten payout and capital-preservation policies before any live-use framing
5. keep the dashboard focused on the four final compared options plus the underlying route mix

## What Counts As Success Now
The success condition is no longer "find more families."

The new success condition is:
- keep a small final option set
- understand why each one wins or loses
- improve capital preservation without collapsing upside
- define where an LLM adds value as an override or controller, not just as another benchmark artifact

## Branch Dependency Graph

### Critical Path
1. current integration and cleanup on `codex/analysis-llm-router-experiment`
2. merge to `main`
3. next hardening lane on `codex/analysis-routing-allocation`

### Parallel Or Secondary Tracks
- [branches/frontend_analysis_portfolio_viz.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/frontend_analysis_portfolio_viz.md)
- [branches/season_playoffs_preseason.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/season_playoffs_preseason.md)
- [branches/season_wnba_bootstrap.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/season_wnba_bootstrap.md)

## Branch Dependency Table

| Branch | Milestone | Depends On | Can Run In Parallel With | Blocks | Notes |
| --- | --- | --- | --- | --- | --- |
| `codex/analysis-llm-router-experiment` | `v1.5.0` | frozen `v1.4.x` strategy and controller work | doc cleanup only | `codex/analysis-routing-allocation` | adds hostile-execution defaults, postseason final 20 validation, and frozen four-option showdown |
| `codex/analysis-routing-allocation` | `v1.5.1` | merged `v1.5.0` state on `main` | season continuity, focused UI | later model/controller work | next critical branch |
| `codex/frontend-analysis-portfolio-viz` | `v1.5.2` | merged `v1.5.0` state | routing/allocation hardening, season continuity | no critical-path branch | should focus on the four finalists and route-mix diagnostics only |
| `codex/season-playoffs-preseason` | `v1.5.3` | merged `v1.5.0` state | routing/allocation, frontend | no critical-path branch | keep data continuity without changing controller math |
| `codex/season-wnba-bootstrap` | `v1.5.x` | safety workflow already merged | routing/allocation, playoffs | no critical-path branch | secondary research lane |

## Stable Risks
- mistaking regular-season 100-game explosions for realistic live expectations
- optimizing LLM prompts before the deterministic router is stabilized under hostile execution
- broadening the family set again instead of improving controller quality
- letting the dashboard drift back into a generic operator page instead of a focused analysis surface
- comparing LLM lanes on different corpora or replay contracts
