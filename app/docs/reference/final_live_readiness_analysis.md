# Final Live Readiness Analysis

> Superseded for live-controller selection by [controller_vnext_final_tuning.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/reference/controller_vnext_final_tuning.md) and [current_analysis_system_state.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/reference/current_analysis_system_state.md). Keep this document as the historical bridge between the older finalist phase and the locked controller-vNext phase.

## Scope

This reference captures the final live-readiness comparison between the two finalist controllers:

- `master_strategy_router_same_side_top1_conf60_v1`
- `gpt-5.4 :: llm_hybrid_freedom_compact_v1`

The goal was to decide whether the live Polymarket integration should remain deterministic or move to an LLM-assisted controller after two improvements:

1. generic portfolio-level risk controls applied at the simulator layer
2. postseason-only historical team-context priors added to the LLM payload

## Contract

- start bankroll: `$10`
- base position fraction floor: `0.20`
- target exposure fraction: `0.80`
- max concurrent positions: `5`
- min order: `$1`
- min shares: `5`
- random adverse slippage: `0-5c`
- postseason reference set: `6` play-in games + `14` playoff games
- postseason seed sweep: `20260422` through `20260427`

## What Was Added

### Portfolio simulator

`app/data/pipelines/daily/nba/analysis/backtests/portfolio.py`

New optional controls:

- run-up throttle
  - `runup_throttle_peak_multiple`
  - `runup_throttle_fraction_scale`
- drawdown throttle
  - `drawdown_throttle_threshold_pct`
  - `drawdown_throttle_fraction_scale`
- hard new-entry drawdown guard
  - `drawdown_new_entry_stop_pct`

These controls are generic and preserve prior behavior when not set.

### LLM postseason priors

`app/data/pipelines/daily/nba/analysis/backtests/llm_experiment.py`

Added compact historical team-context support based on regular-season team profiles:

- win rate
- favorite / underdog rates
- average opening price
- average swing / MFE / MAE
- inversion rate
- favorite drawdown / underdog spike
- confidence mismatch rate
- winner stability rates
- rolling-10 and rolling-20 summaries
- team vs opponent deltas

The prompt explicitly treats `historical_game_context` as a prior, not as certainty.

## Regular-Season Risk Tuning Result

Risk controls were tuned on regular-season `10 / 20 / 50` game samples.

Result:

- `baseline` remained the best overall deterministic portfolio contract
- every tested guard profile reduced drawdown, but the median ending bankroll loss was larger than the drawdown benefit under the current scoring rule

Implication:

- the previously identified run-up / drawdown guards were worth testing
- they are not the default contract for the current finalist router

## Postseason Final 20 Result

### Deterministic finalist

- current and improved deterministic rows were identical because `baseline` won the risk tuning
- mean ending bankroll: about `$22.82`
- mean max drawdown: about `82.34%`
- mean max drawdown amount: about `$20.60`
- mean minimum bankroll: about `$4.42`

### LLM finalist

Four postseason LLM variants were compared:

1. current compact freedom lane
2. risk-only compact freedom lane
3. compact freedom lane with postseason historical context
4. anchored freedom lane with postseason historical context

Key result:

- the best tradeoff variant was `gpt-5.4 :: llm_hybrid_freedom_compact_v1 :: improved_postseason_context`
- mean ending bankroll: about `$17.92`
- median ending bankroll: about `$15.46`
- mean max drawdown: about `44.45%`
- mean max drawdown amount: about `$10.20`
- mean minimum bankroll: about `$7.11`

Interpretation:

- the LLM only showed clear additional value once it received postseason-safe historical priors
- that context sharply reduced path volatility and worst bankroll impairment
- the price paid was lower growth than the deterministic finalist

## Decision Rule

Two valid final candidates remain, with different goals:

### If primary goal is higher bankroll growth

Use:

- `master_strategy_router_same_side_top1_conf60_v1`

Reason:

- higher mean and median postseason ending bankroll than the smoothed LLM-context lane
- simpler live integration
- no model-call cost or runtime dependency

### If primary goal is smoother live path and lower drawdown

Use:

- `gpt-5.4 :: llm_hybrid_freedom_compact_v1 :: improved_postseason_context`

Reason:

- best tradeoff score across the postseason seed sweep
- materially lower mean drawdown and drawdown amount
- meaningfully higher minimum bankroll preservation through the postseason slice

## Recommended Practical Path

For live application integration:

1. ship the deterministic controller as the default execution engine
2. keep the postseason-context LLM lane as the promoted shadow / optional override controller
3. compare both on live paper tracking before enabling LLM selection as the primary controller

This is the most defensible choice because the deterministic lane still wins on raw growth, while the LLM lane now has a specific, demonstrated advantage: smoother behavior when historical postseason-safe priors are available.
