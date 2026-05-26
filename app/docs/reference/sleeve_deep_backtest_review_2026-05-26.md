# Sleeve Deep Backtest Review - 2026-05-26

Status: research artifact summary
Owner issue: #63
Follow-ups: #80, #81

## Artifact

Primary artifact:

`local/shared/artifacts/sleeve-deep-backtests/2026-05-26/20260526T173459Z-deep-pack/sleeve_deep_backtest_review.json`

This pass sampled current NBA regular-season state panels and all available current NBA postseason/play-in panels across five random seeds with `1c` slippage. It also checked WNBA current/prior replay readiness.

## Cohort Coverage

| Cohort | Status | Coverage |
|---|---|---|
| Current NBA regular season | complete | 1,198 available games, 1,379,024 state rows; sampled 120 games per seed across 5 seeds |
| Current NBA postseason/play-in | complete | 44 available games, 51,332 state rows; sampled all postseason/play-in games across 5 seeds |
| Current WNBA regular season | blocked | 37 games, 4,631 PBP rows, 2,399 live snapshots, but 0 market-state rows and 0 price-history rows |
| Prior WNBA season | blocked | no local games/PBP/price history available |

## Sleeve-Level Read

| Cohort | Sleeve | Trade count | Avg return with 1c slippage | Win rate | Min-order PnL |
|---|---|---:|---:|---:|---:|
| NBA regular | core hold | 804 | +13.81% | 52.55% | +$299.71 |
| NBA regular | grid/scalp | 2,384 | +0.81% | 59.04% | +$52.05 |
| NBA regular | ultra-low rebound probe | 998 | -76.04% | 6.96% | -$429.96 |
| NBA postseason | core hold | 185 | +7.67% | 41.58% | +$33.72 |
| NBA postseason | grid/scalp | 495 | +1.39% | 77.37% | +$0.07 |
| NBA postseason | ultra-low rebound probe | 160 | -80.44% | 3.13% | -$81.29 |

## Family-Level Takeaways

Strongest current NBA regular families:

- `inversion` mapped to `core_hold`: 137 trades, +23.35% average return, +$97.03 min-order PnL.
- `q4_clutch` mapped to `grid_scalp`: 24 trades, +18.05% average return, +$16.09 min-order PnL.
- `underdog_liftoff` mapped to `core_hold`: 75 trades, +13.16% average return, +$25.81 min-order PnL.
- `panic_fade_fast` mapped to `grid_scalp`: 116 trades, +5.47% average return, +$29.71 min-order PnL.
- `winner_definition` mapped to `core_hold`: 592 trades, +4.92% average return, +$176.87 min-order PnL.

Strongest current NBA postseason families:

- `q4_clutch` mapped to `grid_scalp`: 10 trades, +25.02% average return, +$8.15 min-order PnL.
- `inversion` mapped to `core_hold`: 35 trades, +15.56% average return, +$17.23 min-order PnL.
- `panic_fade_fast`, `lead_fragility`, and `quarter_open_reprice` were positive but thin in postseason samples.

Negative or quarantine reads:

- `ultra_low_rebound_probe` is strongly negative in both regular and postseason samples. It should stay opt-in/replay-only unless a more constrained rule proves edge.
- `favorite_floor_rebound` and broad `underdog_range_scalp` were negative after slippage in this pack.
- Raw grid/scalp as a sleeve is only mildly positive because strong grid families are diluted by weak families. Grid promotion must be family-specific.

## LLM/ML Routing Read

Current app behavior:

- `gpt-5.4-nano` is routed only for `compression_or_tagging` triggers.
- `gpt-5.4-mini` handles routine live review and normal StrategyPlan revision.
- `gpt-5.5` is reserved for critical/open-exposure/high-uncertainty paths, with budget/exposure downgrades to mini.
- ML trading lane is currently an offline/replay sidecar for candidate ranking, calibrated confidence, execution likelihood, and focus-family gates.

Main gap:

There is no always-on cheap PBP annotation stream feeding sleeve context. #81 owns adding a nano-compatible play-by-play annotator that emits non-executable evidence and escalates only aggregate trigger windows to larger models.

## Development Implications

1. Do not promote naive ultra-low rebound as a default live sleeve from anecdotes alone.
2. Keep current six-sleeve StrategyPlan structure, but make grid/scalp family activation selective.
3. Promote or test `inversion`, `winner_definition`, `underdog_liftoff`, `q4_clutch`, and `panic_fade_fast` as the first evidence-backed sleeve signal families.
4. Treat WNBA backtesting as blocked until #80 populates price history and market-state panels.
5. Use this artifact as pre-window calibration input; it is not live-order permission by itself.
