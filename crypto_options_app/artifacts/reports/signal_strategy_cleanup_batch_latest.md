# Signal And Strategy Cleanup Batch

Generated UTC: `2026-06-07T06:56:17.101006+00:00`
Policy contract: `crypto_options_promotion_policy_contract_v1`

## Summary

- Signal rows: `235`
- Strategy rows: `90`
- Signal classifications: `{"NEEDS_VARIANT": 125, "PROMOTED": 97, "STRICT_REPLAY_REQUIRED": 13}`
- Strategy classifications: `{"BLOCKED": 1, "NEEDS_VARIANT": 54, "SHADOW_REQUIRED": 35}`

## Signal Batch

| Classification | Id | State | Blockers | Next action |
| --- | --- | --- | --- | --- |
| NEEDS_VARIANT | master_hedge_grid_scalping_buy_rebound_cryptoprice_optionprice_pivot_cluster_path_rebound_reclaim_v4 | NEEDS_V2_REVIEW | last_week_backtest_negative_average_forward_return, last_month_backtest_negative_average_forward_return, random_sampling_backtest_negative_average_forward_return, live_shadow_test_negative_average_forward_return | Retire or create a V2-V5 only if it fixes a concrete blocker or source gap. |
| NEEDS_VARIANT | master_hedge_grid_scalping_buy_rebound_cryptoprice_optionprice_pivot_touch_option_reclaim_v1 | NEEDS_V2_REVIEW | last_week_backtest_negative_average_forward_return, last_month_backtest_negative_average_forward_return, random_sampling_backtest_negative_average_forward_return, live_shadow_test_negative_average_forward_return | Retire or create a V2-V5 only if it fixes a concrete blocker or source gap. |
| NEEDS_VARIANT | master_hedge_grid_scalping_buy_rebound_optionprice_favorite_pullback_60c_reclaim_v1 | NEEDS_V2_REVIEW | last_week_backtest_negative_average_forward_return, last_month_backtest_negative_average_forward_return, random_sampling_backtest_negative_average_forward_return, live_shadow_test_negative_average_forward_return | Retire or create a V2-V5 only if it fixes a concrete blocker or source gap. |
| NEEDS_VARIANT | master_hedge_grid_scalping_buy_rebound_optionprice_touch_reclaim_baseline_adjusted_v2 | NEEDS_V2_REVIEW | last_week_backtest_negative_average_forward_return, last_month_backtest_negative_average_forward_return, random_sampling_backtest_negative_average_forward_return, live_shadow_test_negative_average_forward_return | Retire or create a V2-V5 only if it fixes a concrete blocker or source gap. |
| NEEDS_VARIANT | master_hedge_grid_scalping_buy_rebound_optionprice_touch_reclaim_v1 | NEEDS_V2_REVIEW | last_week_backtest_negative_average_forward_return, last_month_backtest_negative_average_forward_return, random_sampling_backtest_negative_average_forward_return, live_shadow_test_negative_average_forward_return | Retire or create a V2-V5 only if it fixes a concrete blocker or source gap. |
| NEEDS_VARIANT | master_hedge_grid_scalping_buy_rebound_optionprice_underdog_10c_to_12c_v1 | NEEDS_V2_REVIEW | last_week_backtest_negative_average_forward_return, last_month_backtest_negative_average_forward_return, random_sampling_backtest_negative_average_forward_return, live_shadow_test_negative_average_forward_return | Retire or create a V2-V5 only if it fixes a concrete blocker or source gap. |
| NEEDS_VARIANT | master_hedge_grid_scalping_buy_rebound_optionprice_underdog_20c_to_25c_v1 | NEEDS_V2_REVIEW | last_week_backtest_negative_average_forward_return, last_month_backtest_negative_average_forward_return, random_sampling_backtest_negative_average_forward_return, live_shadow_test_negative_average_forward_return | Retire or create a V2-V5 only if it fixes a concrete blocker or source gap. |
| NEEDS_VARIANT | master_hedge_grid_scalping_buy_rebound_profiles_optionprice_profile_side_underdog_rebound_v1 | NEEDS_V2_REVIEW | last_week_backtest_negative_average_forward_return, last_month_backtest_negative_average_forward_return, random_sampling_backtest_negative_average_forward_return, live_shadow_test_negative_average_forward_return | Retire or create a V2-V5 only if it fixes a concrete blocker or source gap. |
| NEEDS_VARIANT | master_hedge_grid_scalping_cashout_rebuy_cryptoprice_optionprice_pivot_retrace_cashout_rebuy_v1 | NEEDS_V2_REVIEW | last_week_backtest_negative_average_forward_return, last_month_backtest_negative_average_forward_return, random_sampling_backtest_negative_average_forward_return, live_shadow_test_negative_average_forward_return | Retire or create a V2-V5 only if it fixes a concrete blocker or source gap. |
| NEEDS_VARIANT | master_hedge_grid_scalping_cashout_rebuy_optionprice_favorite_cashout_80c_rebuy_60c_v1 | NEEDS_V2_REVIEW | last_week_backtest_negative_average_forward_return, last_month_backtest_negative_average_forward_return, random_sampling_backtest_negative_average_forward_return, live_shadow_test_negative_average_forward_return | Retire or create a V2-V5 only if it fixes a concrete blocker or source gap. |
| NEEDS_VARIANT | master_hedge_grid_scalping_cashout_rebuy_optionprice_forward_mark_cashout_viability_v5 | NEEDS_V2_REVIEW | last_week_backtest_negative_average_forward_return, last_month_backtest_negative_average_forward_return, random_sampling_backtest_negative_average_forward_return, live_shadow_test_negative_average_forward_return | Retire or create a V2-V5 only if it fixes a concrete blocker or source gap. |
| NEEDS_VARIANT | master_hedge_grid_scalping_cashout_rebuy_optionprice_retrace_ladder_baseline_adjusted_v2 | NEEDS_V2_REVIEW | last_week_backtest_negative_average_forward_return, last_month_backtest_negative_average_forward_return, random_sampling_backtest_negative_average_forward_return, live_shadow_test_negative_average_forward_return | Retire or create a V2-V5 only if it fixes a concrete blocker or source gap. |

## Strategy Batch

| Classification | Id | State | Blockers | Next action |
| --- | --- | --- | --- | --- |
| NEEDS_VARIANT | a_fallback_outcome_probe_v1 | SHADOW_REVIEW | shadow_live_non_positive_pnl, missing_recent_shadow_live_economic_evidence | Revise or retire; do not rerun unchanged failed strategy lanes. |
| NEEDS_VARIANT | buying_ahead_pre_event_v1 | SHADOW_REVIEW | shadow_live_non_positive_pnl, missing_recent_shadow_live_economic_evidence | Revise or retire; do not rerun unchanged failed strategy lanes. |
| NEEDS_VARIANT | crypto_direction_option_context_hold_60s_v1 | SHADOW_REVIEW | shadow_live_non_positive_pnl, missing_recent_shadow_live_economic_evidence | Revise or retire; do not rerun unchanged failed strategy lanes. |
| NEEDS_VARIANT | crypto_observer_contrarian_option_hold_60s_v1 | SHADOW_REVIEW | missing_historical_strategy_replay, shadow_live_non_positive_pnl, missing_recent_shadow_live_economic_evidence | Revise or retire; do not rerun unchanged failed strategy lanes. |
| NEEDS_VARIANT | crypto_observer_fade_option_scalp_v1 | SHADOW_REVIEW | missing_historical_strategy_replay, shadow_live_non_positive_pnl, missing_recent_shadow_live_economic_evidence | Revise or retire; do not rerun unchanged failed strategy lanes. |
| NEEDS_VARIANT | crypto_observer_follow_option_scalp_v1 | SHADOW_REVIEW | missing_historical_strategy_replay, shadow_live_non_positive_pnl, missing_recent_shadow_live_economic_evidence | Revise or retire; do not rerun unchanged failed strategy lanes. |

## Safety

- This batch is read-only.
- It does not authorize orders or live trading.
- `PROMOTED` here means cleanup classification only; live promotion still requires the promotion manager and supervised runtime gates.
