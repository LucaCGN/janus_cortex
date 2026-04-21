# NBA Analysis Next Steps

## End Goal
Turn the current offline NBA analysis stack into a repeatable trading-research program that can:
- classify research-ready versus descriptive-only games deterministically
- benchmark several strategy families under the same sequential bankroll contract
- route between surviving families with deterministic context rules
- compare deterministic routing with later statistical model layers
- surface the final outputs in read-only tooling before any live decision layer is considered

Detailed milestone and branch decomposition lives in:
- [roadmap_to_multi_algo_backtests.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/roadmap_to_multi_algo_backtests.md)
- [branches/README.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/README.md)

## What Is Already Done
- `A0-A7` implementation wave completed
- release wave completed through `v1.4.2`
- current CLI surface is stable:
  - `build_analysis_mart`
  - `build_analysis_report`
  - `run_analysis_backtests`
  - `train_analysis_baselines`
- current promoted keep families:
  - `inversion`
  - `winner_definition`
  - `underdog_liftoff`

## Frozen Current State
- research-ready games: `1198 / 1224`
- descriptive-only games still visible: `26`
- benchmark contract: `v6`
- bankroll contract:
  - start `$10.00`
  - position fraction `1.0`
  - first `100` chronological games
  - one open position at a time

## Immediate Critical Path

### 1. Deterministic Routing And Allocation
Branch:
- `codex/analysis-routing-allocation`

Why this is next:
- the three keep families now exist
- the main remaining gap is overlap friction and family selection, not raw family discovery

Target outputs:
- promoted routed sleeve or priority stack
- overlap-cost diagnostics
- routed robustness, not just single-family robustness

### 2. Context Models Around The Winners
Branch:
- `codex/analysis-context-models`

Why this follows:
- once the deterministic routing baseline is frozen, model work can be judged against a real control instead of against intuition

Target outputs:
- continuation-quality models for `inversion`
- persistence or reopen-risk models for `winner_definition`
- target-hit or stop-hit models for `underdog_liftoff`
- route-score baselines for portfolio selection

### 3. Read-Only Portfolio Visualization
Branch:
- `codex/frontend-analysis-portfolio-viz`

Why this is parallel-friendly:
- it can consume frozen artifacts after routing/allocation rules are fixed
- it should not block model work once the contracts are stable

Target outputs:
- portfolio rankings
- robustness tables
- route maps
- overlap diagnostics

## Parallel Sidecars
- `codex/season-playoffs-preseason`
- `codex/season-wnba-bootstrap`

These remain secondary and should not block the portfolio-routing or model path.

## Product Questions To Keep Answering
- which family should own a game before tipoff and after the game state changes?
- where is the true cost of overlap and blocked trades?
- can a simple statistical model improve routing or trade-quality ranking without overfitting?
- when do underdog rebounds deserve continuation treatment versus inversion treatment?
- which outputs need to be visible in the studio before any later human or LLM review layer is considered?
