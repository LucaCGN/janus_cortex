# Controller vNext Final Tuning

## Snapshot
- date: `2026-04-22`
- purpose: final playoff-controller tuning pass for the strategy that is intended to move from backtest into live Polymarket execution

## What Was Tuned
`Controller vNext` implemented the remaining high-signal hardening levers on top of the current finalists:

1. uncertainty-band LLM review instead of broad weak-game review
2. mandatory `winner_definition` overlay stop at `5c` below entry
3. per-trade sizing overrides based on:
   - family type
   - sleeve/core role
   - deterministic or LLM confidence
4. family and sleeve caps through per-trade sizing caps
5. portfolio-level guards in the simulator:
   - run-up throttle
   - drawdown throttle
   - hard drawdown new-entry stop
   - daily-loss new-entry stop

## Benchmark Contract
- start bankroll: `$10.00`
- base position fraction floor: `20%`
- target exposure fraction: `80%`
- max concurrent positions: `5`
- concurrency mode: `shared_cash_equal_split`
- min order: `$1`
- min shares: `5`
- random adverse slippage: `0-5c`

Validation surfaces:
- regular-season random windows: `10`, `20`, `50` games
- `10` windows per size
- fixed postseason reference: `6` play-in + `14` playoff games
- postseason slippage seeds: `20260422` through `20260427`

## Variants Compared
- `master_current_v1`
- `unified_current_v1`
- `controller_vnext_deterministic_v1 :: balanced`
- `controller_vnext_deterministic_v1 :: tight`
- `controller_vnext_unified_v1 :: balanced`
- `controller_vnext_unified_v1 :: tight`

## Main Result
If the goal is the actual upcoming playoff games, the best live candidate is:

- `controller_vnext_unified_v1 :: balanced`

Reason:
- it gave the best postseason tradeoff score
- it preserved a positive ending bankroll across all tested slippage seeds
- it cut drawdown far below the current finalists while staying profitable

Postseason reference:
- `controller_vnext_unified_v1 :: balanced`
  - mean ending bankroll: `$13.84`
  - median ending bankroll: `$13.81`
  - mean max drawdown: `22.38%` / `$2.24`
  - mean minimum bankroll: `$7.76`

Compared with current finalists:
- `master_current_v1`
  - mean ending bankroll: `$21.90`
  - median ending bankroll: `$21.62`
  - mean max drawdown: `82.12%` / `$20.02`
- `unified_current_v1`
  - mean ending bankroll: `$21.07`
  - median ending bankroll: `$20.77`
  - mean max drawdown: `82.01%` / `$18.99`

Interpretation:
- the current finalists still have higher raw upside
- but that upside is driven by much more violent bankroll paths
- for real playoff deployment, the vNext unified controller has the best risk-adjusted profile

## Deterministic Fallback
If no LLM should be used live, the best deterministic fallback is:

- `controller_vnext_deterministic_v1 :: tight`

Postseason reference:
- mean ending bankroll: `$14.35`
- median ending bankroll: `$14.33`
- mean max drawdown: `29.39%` / `$4.43`
- mean minimum bankroll: `$7.92`

This is weaker than the vNext unified controller on tradeoff score, but much safer than `master_current_v1`.

## Important Finding About The New Guards
The portfolio-level guards were implemented and benchmarked, but under the winning vNext candidates they did not become the dominant smoothing mechanism.

Observed effect:
- `mean_skipped_risk_guard_count = 0`
- `mean_skipped_daily_loss_guard_count = 0`

That means the main improvements came from:
1. uncertainty-band LLM review
2. `winner_definition` overlay stop
3. per-trade family/confidence sizing caps

So the portfolio guards are now available for future hardening, but they are not the primary reason the winning controller improved.

## Practical Recommendation
For the next live playoff phase:

1. use `controller_vnext_unified_v1 :: balanced` as the primary controller
2. keep `controller_vnext_deterministic_v1 :: tight` as the no-LLM fallback
3. continue logging every decision with:
   - deterministic default
   - LLM review status
   - selected family
   - confidence
   - stop-overlay trigger
   - final stake fraction

## Full Regular-Season Lock Check
Before locking the controller pair, both kept candidates were replayed across the full all-games `2025-26` regular-season corpus under the same contract and the same slippage seed set.

### Primary controller
- `controller_vnext_unified_v1 :: balanced`
  - median ending bankroll: `$469,835.30`
  - mean ending bankroll: `$463,984.42`
  - range: `$382,670.48` to `$542,845.91`
  - mean max drawdown: `70.41%` / `$225,232.21`
  - mean minimum bankroll: `$4.08`
  - entered about `925` games

### Deterministic fallback
- `controller_vnext_deterministic_v1 :: tight`
  - median ending bankroll: `$68,486.79`
  - mean ending bankroll: `$70,940.38`
  - range: `$63,066.40` to `$85,241.79`
  - mean max drawdown: `71.09%` / `$29,438.87`
  - mean minimum bankroll: `$3.59`
  - entered about `1193` games

Interpretation:
- the primary controller remained strongly profitable on the full regular season
- the lock decision is not just a postseason slice overfit
- the primary controller is now frozen for live execution work, with the deterministic tight controller kept as the explicit fallback

## Artifacts
- local report: `C:\code-personal\janus-local\janus_cortex\archives\output\nba_analysis_controller_vnext\2025-26\controller_vnext_dynamic80_s5_slip5\controller_vnext_report.md`
- regular summary: `C:\code-personal\janus-local\janus_cortex\archives\output\nba_analysis_controller_vnext\2025-26\controller_vnext_dynamic80_s5_slip5\controller_vnext_regular_summary.csv`
- postseason summary: `C:\code-personal\janus-local\janus_cortex\archives\output\nba_analysis_controller_vnext\2025-26\controller_vnext_dynamic80_s5_slip5\controller_vnext_postseason_summary.csv`
- overall ranking: `C:\code-personal\janus-local\janus_cortex\archives\output\nba_analysis_controller_vnext\2025-26\controller_vnext_dynamic80_s5_slip5\controller_vnext_overall_summary.csv`
- full regular-season lock check:
  - `C:\code-personal\janus-local\janus_cortex\archives\output\nba_analysis_controller_vnext\2025-26\controller_vnext_regular_full_season_all_games\regular_full_season_all_games_report.md`
  - `C:\code-personal\janus-local\janus_cortex\archives\output\nba_analysis_controller_vnext\2025-26\controller_vnext_regular_full_season_all_games\regular_full_season_all_games_summary.csv`
