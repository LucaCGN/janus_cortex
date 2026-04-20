# Sequential Portfolio Benchmarking Reference

## Purpose
Define the canonical bankroll-simulation contract for the NBA analysis module and freeze the current validated outcomes for:
- single-family sequential replay
- repeated-seed robustness across the full six-family set
- the combined keep-family sleeve
- the opening-band statistical routing lane

## Canonical Contract
Sequential portfolio benchmarking now extends `run_analysis_backtests` under benchmark contract `v5`.

Active defaults:
- starting bankroll: `10.0`
- position size fraction: `1.0`
- game limit: `100`
- overlap policy: one position at a time, later entries are skipped while capital is already committed
- split set:
  - `full_sample`
  - `time_train`
  - `time_validation`
  - `random_train`
  - `random_holdout`
- explicit robustness seeds used in the current validation pass:
  - `1107`
  - `2113`
  - `3251`
  - `4421`
  - `5573`
  - `6659`
  - `7873`
  - `9011`
  - `10243`
  - `11519`

Determinism rules:
- trade order is sorted by `entry_at`, then stable game, side, family, and state-index tie-breakers
- the game window is the first `100` chronological games in the chosen sample
- final-state entries are discarded rather than converted into zero-hold trades
- the same sample and seed produce the same bankroll path

## Portfolio Artifacts
Each benchmarked backtest run emits:
- `benchmark_portfolio_summary`
- `benchmark_portfolio_steps`
- `benchmark_portfolio_candidate_freeze`
- `benchmark_portfolio_robustness_detail`
- `benchmark_portfolio_robustness_summary`
- `benchmark_route_summary`

Key portfolio metrics:
- `ending_bankroll`
- `total_pnl_amount`
- `compounded_return`
- `max_drawdown_amount`
- `max_drawdown_pct`
- `executed_trade_count`
- `skipped_overlap_count`
- `skipped_bankroll_count`

## Current Validated Family Status
Validated on `2026-04-20` against season `2025-26`, phase `regular_season`, with a `$10.00` starting bankroll and `1.0` position fraction.

### `inversion`
- how it works:
  - buy the underdog once it crosses `45c` for deeper openers below `25c`, otherwise on the standard `50c` cross
  - require positive recent momentum and avoid badly trailing scoreboard states
  - exit if the path breaks back below `48c` or the game ends
- full-sample result:
  - ending bankroll: `46420820.82`
  - max drawdown: `29.11%`
  - executed trades: `79`
- 10-seed robustness:
  - label: `stable_positive`
  - positive seeds: `10 / 10`
  - ending bankroll range: `165.26` to `110720.97`
  - median ending bankroll: `2479.15`
  - worst drawdown: `56.27%`

### `winner_definition`
- how it works:
  - buy once the market reaches `80c`
  - widen the break line from `75c` to `76c` only when the entry score margin is already `8+`
  - otherwise keep the standard `75c` break or hold to the end
- full-sample result:
  - ending bankroll: `2490.05`
  - max drawdown: `24.49%`
  - executed trades: `50`
- 10-seed robustness:
  - label: `stable_positive`
  - positive seeds: `10 / 10`
  - ending bankroll range: `45249.66` to `1626989.21`
  - median ending bankroll: `141733.35`
  - worst drawdown: `52.38%`

### `underdog_liftoff`
- how it works:
  - buy an underdog opener below `45c` once it confirms strength through `38c`
  - require positive recent momentum, at least `900` seconds left, and no worse than a `2` point deficit
  - exit at `50c`, on a `4c` stop, or at the end
- full-sample result:
  - ending bankroll: `82867.67`
  - max drawdown: `69.13%`
  - executed trades: `96`
- 10-seed robustness:
  - label: `mixed`
  - positive seeds: `9 / 10`
  - ending bankroll range: `8.00` to `73.39`
  - median ending bankroll: `25.22`
  - worst drawdown: `48.19%`

### `reversion`
- full-sample result:
  - ending bankroll effectively goes to zero
  - max drawdown: `100.00%`
- 10-seed robustness:
  - label: `stable_negative`
  - positive seeds: `0 / 10`
  - median ending bankroll: effectively `0`
  - worst drawdown: `100.00%`

### `comeback_reversion`
- full-sample result:
  - ending bankroll: `0.0187`
  - max drawdown: `99.84%`
- 10-seed robustness:
  - label: `mixed`
  - positive seeds: `1 / 10`
  - median ending bankroll: `0.6487`
  - worst drawdown: `99.99%`

### `volatility_scalp`
- full-sample result:
  - ending bankroll: `0.0034`
  - max drawdown: `99.97%`
- 10-seed robustness:
  - label: `mixed`
  - positive seeds: `1 / 10`
  - median ending bankroll: `2.62`
  - worst drawdown: `88.14%`

## Combined Keep-Family Sleeve
The current keep sleeve merges the three promoted single-family candidates:
- members: `inversion,underdog_liftoff,winner_definition`
- combined family label: `combined_keep_families`

Current result:
- full-sample ending bankroll: `58310.08`
- full-sample compounded return: `5830.01`
- full-sample max drawdown: `33.09%`
- full-sample executed trades: `63`
- full-sample skipped overlaps: `97`
- random-holdout ending bankroll: `12524360.00`
- random-holdout compounded return: `1252435.00`

Interpretation:
- the combined sleeve is still useful as a diversification surface
- it does not beat pure `inversion` on terminal bankroll because overlap collisions suppress the highest-upside inversion path
- the next portfolio question is weighting and priority, not sleeve existence

## Statistical Routing
The first routing lane learns an opening-band family choice from `time_train` and replays that map across every split.

Current opening-band map:
- `10-20`: `inversion`
- `20-30`: `inversion`
- `30-40`: `inversion`
- `40-50`: `inversion`
- `50-60`: `winner_definition`
- `60-70`: `winner_definition`
- `70-80`: `winner_definition`
- `80-90`: `winner_definition`
- `90-100`: `winner_definition`

Current routed result:
- full-sample ending bankroll: `22480.43`
- full-sample max drawdown: `31.20%`
- random-holdout ending bankroll: `1330294.00`

Interpretation:
- the training split does support simple game categorization by opening band
- underdog bands still route to `inversion`, not `underdog_liftoff`
- `underdog_liftoff` remains a profitable standalone family, but not the preferred opening-band router when inversion is available

## Final Improvement Pass
Retained strategy changes from this pass:
- `inversion`
  - dynamic deeper entry for the lowest opener tier
  - positive momentum and scoreboard guardrails
  - tighter `48c` protection line
- `winner_definition`
  - slightly wider break line only in stronger scoreboard-control states
- `underdog_liftoff`
  - new underdog continuation family in the `38c -> 50c` zone
- `statistical_routing_v1`
  - training-derived opening-band family selection

Kept as historical rejections:
- `inversion 52c/48c`
- `winner_definition 82c/77c`
- `winner_definition` two-state `80c` confirmation

## Promotion Rule
Per-trade average return is not enough to promote a family.

Promotion now requires:
- positive sequential full-sample result
- positive sequential random-holdout result
- acceptable sequential drawdown
- repeated-seed behavior that is at least directionally defensible under the same bankroll contract

## Statistical And LLM Boundary
The LLM layer is not part of the benchmark contract.

What remains purely statistical:
- keep/drop decisions
- repeated-seed robustness
- drawdown and tail-risk comparisons
- opening-band family routing
- future sleeve weight selection

Where a later statistical-plus-LLM review layer may help:
- explain why `inversion` dominates underdog opening bands
- summarize recurring failure traces for `underdog_liftoff`
- propose the next parameter grids after the statistical gate identifies a weak boundary

## Next Follow-On Questions
- should robustness expand to the routed sleeve and the combined sleeve directly?
- should allocation work prioritize `inversion` while letting `winner_definition` and `underdog_liftoff` act as capped side sleeves?
- should the next modeling lane learn route selection from opening band, early momentum, and score-state features instead of fixed band rules?
