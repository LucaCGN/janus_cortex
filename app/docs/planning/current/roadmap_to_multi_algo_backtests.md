# Roadmap To Locked Controller Deployment

## Purpose
Define the path from the offline NBA analysis baseline to the first live-safe controller release where:
- the controller pair is locked
- hostile execution assumptions are already benchmarked
- the live executor uses one primary policy and one deterministic fallback
- every live and paper decision is logged in an ML-ready format

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
| `v1.4.0` | sequential portfolio benchmark | completed | archived |
| `v1.4.1` | repeated-seed robustness and combined keep-family sleeve | completed | archived |
| `v1.4.2` | refined underdog continuation and first routing lane | completed | archived |
| `v1.4.3` | realistic execution replay and quarter-specific sleeves | completed | archived |
| `v1.4.4` | master-router baseline and expanded family research | completed | archived |
| `v1.4.5` | restrained LLM router benchmark and finalist dashboard | completed | archived |
| `v1.4.6` | postseason event coverage and adverse slippage contract | completed | archived into current references |
| `v1.4.7` | controller-vNext playoff tuning | completed | primary live candidate and fallback identified |
| `v1.4.8` | full regular-season lock check | completed | lock confirmed on all-games replay |
| `v1.5.0` | live Polymarket executor | next critical path | primary controller plus deterministic fallback |
| `v1.5.1` | controller decision logging | next after executor shell exists | ML-ready candidate and fill dataset |
| `v1.5.2` | focused locked-controller review dashboard | parallel after executor contracts stabilize | review only, not broad exploration |
| `v1.5.3` | live-paper validation and payout policy hardening | after executor plus logging | real execution feedback loop |
| `v1.5.x` | season continuity and WNBA bootstrap | secondary | keep data continuity without changing controller math |

## Locked Analysis Baseline

### Locked Controllers
- primary live candidate:
  - `controller_vnext_unified_v1 :: balanced`
- deterministic fallback:
  - `controller_vnext_deterministic_v1 :: tight`

### Underlying Strategy Stack
These are the underlying families still used by the locked controllers:
- core routed families:
  - `winner_definition`
  - `inversion`
  - `underdog_liftoff`
- independent trigger sleeves:
  - `q1_repricing`
  - `q4_clutch`

Everything else is now historical research only.

### Locked Execution Contract
Current locked replay defaults:
- initial bankroll: `$10.00`
- base position fraction floor: `20%`
- target exposure fraction: `80%`
- maximum concurrent positions: `5`
- concurrency mode: `shared_cash_equal_split`
- sizing mode: `dynamic_concurrent_games`
- minimum order: `$1.00`
- minimum shares: `5`
- deterministic slippage: `0c`
- random adverse slippage: `0-5c`
- key stop overlay:
  - `winner_definition` `5c` below entry

## Locked Validation Set

### Postseason hard validation
- fixed `20`-game corpus:
  - `6` play-in games
  - `14` playoff games
- all `20` are research-ready
- all `20` have linked Polymarket history
- all `20` have play-by-play loaded

Reference:
- [postseason_final_20_validation.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/reference/postseason_final_20_validation.md)

### Full regular-season lock check
- corpus size:
  - `1198` research-ready regular-season games
- purpose:
  - confirm the locked playoff-tuned controller does not fail on the broader season corpus

## Current Scoreboard

### Postseason reference
- `controller_vnext_unified_v1 :: balanced`
  - mean ending bankroll: `$13.84`
  - median: `$13.81`
  - mean drawdown: `22.38%` / `$2.24`
- `controller_vnext_deterministic_v1 :: tight`
  - mean ending bankroll: `$14.35`
  - median: `$14.33`
  - mean drawdown: `29.39%` / `$4.43`

### Full regular-season lock check
- `controller_vnext_unified_v1 :: balanced`
  - median ending bankroll: `$469,835.30`
  - mean ending bankroll: `$463,984.42`
  - mean drawdown: `70.41%` / `$225,232.21`
- `controller_vnext_deterministic_v1 :: tight`
  - median ending bankroll: `$68,486.79`
  - mean ending bankroll: `$70,940.38`
  - mean drawdown: `71.09%` / `$29,438.87`

Interpretation:
- the primary controller is the best playoff tradeoff and remains strongly profitable on the full regular season
- the fallback remains viable and simpler if no LLM should be used live

## Critical Path From Here
1. wire the locked primary controller into a live-safe Polymarket executor
2. keep the deterministic fallback available behind configuration or operator override
3. log every candidate, skip, override, stop, and fill outcome
4. use those logs to build the ML-ready candidate dataset
5. keep the dashboard focused on the locked controller pair and route diagnostics only

## What Counts As Success Now
The success condition is no longer "find better families."

The success condition is:
- execute the locked controller safely
- preserve the bounded fallback path
- gather enough live or paper data to measure real execution quality
- produce the dataset needed for later ML ranking, skip, and sizing models

## Branch Dependency Graph

### Critical Path
1. merge the locked-controller docs and state to `main`
2. launch `codex/live-polymarket-executor`
3. launch `codex/controller-decision-logging`

### Parallel Or Secondary Tracks
- [branches/frontend_analysis_portfolio_viz.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/frontend_analysis_portfolio_viz.md)
- [branches/season_playoffs_preseason.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/season_playoffs_preseason.md)
- [branches/season_wnba_bootstrap.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/season_wnba_bootstrap.md)

## Branch Dependency Table

| Branch | Milestone | Depends On | Can Run In Parallel With | Blocks | Notes |
| --- | --- | --- | --- | --- | --- |
| `codex/live-polymarket-executor` | `v1.5.0` | locked controller state merged on `main` | docs cleanup only | `codex/controller-decision-logging` | first live-safe paper or execution shell |
| `codex/controller-decision-logging` | `v1.5.1` | executor contracts stabilized | frontend review UI, season continuity | later ML ranking work | append-only candidate and fill datasets |
| `codex/frontend-analysis-portfolio-viz` | `v1.5.2` | locked controller state on `main` | executor and logging | no critical-path branch | should only review the locked controller pair |
| `codex/season-playoffs-preseason` | `v1.5.3` | merged locked state | executor, frontend | no critical-path branch | keep season continuity without changing controller math |
| `codex/season-wnba-bootstrap` | `v1.5.x` | safety workflow already merged | executor, playoffs | no critical-path branch | secondary research lane |

## Stable Risks
- mistaking backtest compounding explosions for realistic live expectations
- overusing the LLM after the primary controller is already locked
- changing the controller pair before live or paper execution feedback exists
- letting the dashboard drift back into broad strategy-lab exploration
- failing to log enough detail to build the later ML-ready candidate dataset
