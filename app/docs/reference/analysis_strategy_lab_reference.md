# Analysis Strategy Lab Reference

## Purpose
Describe the shared backtest strategy contract that entered the strategy-lab release.

This is the reference for:
- which strategy families exist
- what metadata each family exposes
- which artifacts the backtest runner emits for each family

## Shared Strategy Contract
Every strategy family now resolves through one registry contract with:
- `family`
- `entry_rule`
- `exit_rule`
- `description`
- `comparator_group`
- `tags`
- `simulator`

Every family emits the same trade-row schema and summary metrics.

## Current Candidate Families

| Family | Entry Rule | Exit Rule | Intent |
| --- | --- | --- | --- |
| `reversion` | `favorite_drawdown_buy_10c` | `reclaim_open_minus_2c_or_end` | favorite drawdown reversion |
| `inversion` | `first_cross_above_50c` | `break_back_below_50c_or_end` | underdog inversion continuation |
| `winner_definition` | `reach_80c` | `break_75c_or_end` | winner-definition continuation |
| `comeback_reversion` | `q2_q3_underdog_trail_buy_rebound` | `plus_8c_or_minus_6c_or_end` | underdog comeback reversion in Q2 or Q3 |
| `volatility_scalp` | `q1_midband_drawdown_scalp` | `partial_reclaim_or_minus_5c_or_end` | opening-band Q1 volatility scalp |

## Artifact Contract
The backtest runner now emits:
- shared family summary frame
- per-family trade frame
- per-family best-trade frame
- per-family worst-trade frame
- per-family context summary frame
- per-family trade trace JSON

This makes the strategy lab inspectable enough for:
- benchmarking
- operator review
- later frontend consumption

## Trace Content
Each trace captures:
- game and side identity
- entry and exit indices
- entry and exit timestamps
- slippage-adjusted return
- surrounding state-panel rows near the trade window

State rows include:
- `state_index`
- `event_at`
- `period_label`
- `score_diff`
- `score_diff_bucket`
- `context_bucket`
- `team_price`
- `opening_price`
- `price_delta_from_open`
- `net_points_last_5_events`

## Promotion Note
This branch freezes the comparable strategy surface for the next benchmarking lane.

The next branch should treat this registry and artifact shape as the benchmark input contract unless there is a documented reason to revise it.
