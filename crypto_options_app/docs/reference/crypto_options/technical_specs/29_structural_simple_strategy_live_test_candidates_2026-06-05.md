# Structural Simple Strategy Live-Test Candidates

Date: 2026-06-05

Status: read-only design checkpoint. These are structural strategy candidates for future supervised live validation. This document does not authorize live orders.

## Scope

The Signal Backtest Lab now has 152 signal variants:

- 152 `PASSED` queue rows.
- 129 `STRUCTURAL_PASS`.
- 23 `NEEDS_V2_REVIEW`.
- 0 `PROMOTION_READY`.

The goal here is not to build `master_hedge_grid_scalping` yet. The goal is to define a broad but simple live-test roster so we can validate mechanics one at a time once strict replay and operator approval allow supervised live validation again.

## Selection Rules

- Use only `STRUCTURAL_PASS` signals.
- Avoid the 23 `NEEDS_V2_REVIEW` signals as primary directional inputs.
- Signal variants may go up to V5, but this roster does not create V4/V5 variants just to increase count.
- Prefer simple strategy shells with one primary signal, one guard, and one lifecycle rule.
- Do not combine more than three signal families unless the candidate is explicitly a confluence test.
- Treat all candidates as structural validation candidates, not profit claims.
- Live testing must remain supervised, budget-capped, reconciled, and disabled by default until explicitly started through the approved runner.

## Common Live-Test Harness

Every candidate below should use the same harness shape when it later becomes executable:

| Field | Default |
| --- | --- |
| Mode | supervised structural live validation only |
| Order size | minimal executable notional unless candidate says otherwise |
| Symbols | BTC, ETH |
| Event scope | 5-minute Up/Down crypto events |
| Max event cycles | 3 for initial smoke, 10 for comparison after clean smoke |
| Per-candidate cap | start with low notional, then increase only after mechanical clean pass |
| Stop on | reconciliation mismatch, missing lifecycle coverage, stale data, malformed candidate, duplicate cadence, executor boundary failure |
| Performance stop | lane-level only after enough settled positions; underperformance does not trigger code patching |

All candidates must include candidate -> intent -> order -> fill/unfilled -> position -> exit plan lifecycle coverage, exact CLOB/order audit when an exchange order exists, canonical DB persistence, and dashboard/report attribution by candidate id.

## Candidate Roster

| # | Candidate | Primary signal | Guard | Mechanic | Initial value |
| ---: | --- | --- | --- | --- | --- |
| 1 | `simple_profile_cost_distribution_hold_v1` | `outcome_prediction_profiles_top_distribution_cost_weighted_v1` | `latency_quality_abc_freshness_gate_v1` | Buy dominant cost-weighted profile side after 0:30 snapshot; hold. | High |
| 2 | `simple_profile_shares_distribution_hold_v1` | `outcome_prediction_profiles_top_distribution_shares_weighted_v1` | `latency_quality_abc_freshness_gate_v1` | Buy dominant share-weighted profile side; hold. | High |
| 3 | `simple_profile_count_consensus_hold_v1` | `outcome_prediction_profiles_top_distribution_count_consensus_v1` | `liquidity_depth_top_depth_slippage_v1` | Buy only on broad profile-count consensus; hold. | Medium |
| 4 | `simple_pre_event_profile_side_start_v1` | `side_start_profiles_pre_event_distribution_v1` | `latency_quality_abc_freshness_gate_v1` | Enter one side pre-event when buying-ahead distribution is decisive. | High |
| 5 | `simple_profile_mark_to_distribution_hedge_v1` | `hedge_ratio_profiles_optionprice_mark_to_distribution_v1` | `liquidity_depth_top_depth_slippage_v1` | Target a simple Up/Down ratio from profile distribution and current option prices; one rebalance max. | High |
| 6 | `simple_profile_cost_ratio_hedge_v1` | `hedge_ratio_profiles_distribution_cost_weighted_v1` | `stale_order_review_spread_latency_v1` | Buy toward cost-weighted profile ratio, no scalping. | High |
| 7 | `simple_profile_entropy_rebalance_v1` | `hedge_ratio_profiles_entropy_reduced_rebalance_v1` | `liquidity_depth_profile_pressure_depth_guard_v1` | Rebalance only when profile distribution entropy falls. | Medium |
| 8 | `simple_option_first_10s_momentum_hold_v1` | `outcome_prediction_optionprice_first_10s_momentum_v1` | `liquidity_depth_top1_depth_min_notional_v1` | After first 10s, buy stronger option side and hold. | High |
| 9 | `simple_option_first_30s_reversal_guard_hold_v1` | `outcome_prediction_optionprice_first_30s_momentum_reversal_guard_v1` | `stale_order_review_spread_latency_v1` | Buy first-30s momentum side only if reversal guard is not active. | High |
| 10 | `simple_option_first_60s_favorite_hold_v1` | `outcome_prediction_optionprice_first_60s_favorite_hold_v1` | `final_minute_near_resolved_skip_underdog_v1` | Enter favorite after 60s and hold; avoid late unresolved comeback exposure. | Medium |
| 11 | `simple_option_pre_event_drift_side_start_v1` | `side_start_optionprice_pre_event_drift_v1` | `liquidity_depth_top_depth_slippage_v1` | Enter pre-event drifting side before event start, no grid. | High |
| 12 | `simple_option_touch_reclaim_rebound_v1` | `buy_rebound_optionprice_touch_reclaim_v1` | `support_resistance_optionprice_bucket_rebound_v1` | Buy option bucket touch/reclaim; sell on small fixed target. | High |
| 13 | `simple_underdog_10c_rebound_scalp_v1` | `buy_rebound_optionprice_underdog_10c_to_12c_v1` | `liquidity_depth_top3_depth_slippage_guard_v1` | Buy underdog near 10c only when liquidity allows; sell around 12c. | Medium |
| 14 | `simple_underdog_20c_rebound_scalp_v1` | `buy_rebound_optionprice_underdog_20c_to_25c_v1` | `support_resistance_optionprice_20c_underdog_rebound_v1` | Buy underdog around 20c; sell near 25c. | High |
| 15 | `simple_favorite_pullback_reclaim_v1` | `buy_rebound_optionprice_favorite_pullback_60c_reclaim_v1` | `stale_order_review_cancel_final_90s_bad_spread_v1` | Buy favorite pullback around 60c after reclaim. | Medium |
| 16 | `simple_profile_confirmed_underdog_rebound_v1` | `buy_rebound_profiles_optionprice_profile_side_underdog_rebound_v1` | `hedge_ratio_profiles_optionprice_cost_to_price_mark_v1` | Take underdog rebound only when profile pressure supports that side. | High |
| 17 | `simple_retrace_ladder_cashout_rebuy_v1` | `cashout_rebuy_optionprice_retrace_ladder_v1` | `stale_order_review_replace_after_30s_spread_widen_v1` | Buy, cash out at target, place one rebuy lower; no multi-level ladder. | High |
| 18 | `simple_favorite_80_60_cashout_rebuy_v1` | `cashout_rebuy_optionprice_favorite_cashout_80c_rebuy_60c_v1` | `liquidity_depth_top_depth_slippage_min_notional_v2` | Cash out favorite around 80c and rebuy around 60c if retrace appears. | Medium |
| 19 | `simple_option_volatility_grid_spacing_v1` | `grid_spacing_optionprice_rolling_volatility_depth_v1` | `grid_count_optionprice_depth_budget_v1` | Tiny two-level grid using option rolling volatility and depth-budget count. | High |
| 20 | `simple_30s_range_grid_spacing_v1` | `grid_spacing_optionprice_30s_range_scaled_v1` | `grid_type_optionprice_inversion_frequency_v1` | Use first 30s option range to set grid spacing; only run when inversion frequency supports grid. | Medium-high |
| 21 | `simple_inversion_frequency_grid_type_v1` | `grid_type_optionprice_inversion_frequency_v1` | `support_resistance_optionprice_40_60_midrange_inversion_v1` | Choose buy/sell grid only in midrange inversion-heavy paths. | Medium |
| 22 | `simple_stale_order_review_only_v1` | `stale_order_review_optionprice_spread_latency_v1` | `latency_quality_abc_freshness_gate_v1` | Simple limit entry with review/cancel/replace at stale-order threshold. | High |
| 23 | `simple_final_minute_skip_resolved_v1` | `final_minute_optionprice_near_resolved_skip_underdog_v1` | `liquidity_depth_top_depth_slippage_v1` | Avoid underdog final-minute trades when market is effectively resolved. | Shadow-first |
| 24 | `simple_final_minute_underdog_comeback_v1` | `final_minute_optionprice_underdog_10_25c_comeback_v1` | `final_minute_optionprice_profiles_comeback_probability_v1` | Tiny underdog comeback exposure only when option and profile final-minute signals agree. | Low initially |
| 25 | `simple_crypto_option_confirmed_momentum_hold_v1` | `outcome_prediction_cryptoprice_optionprice_ema_slope_1m_5m_agreement_option_confirmed_v2` | `latency_quality_observer_freshness_consensus_abc_freshness_required_v2` | Use crypto trend only when option price confirms; hold selected side. | Medium |

## Initial Live-Test Priority

The first live-test bundle should not include all candidates. The best initial structural set is:

| Priority | Candidate | Why |
| ---: | --- | --- |
| 1 | `simple_profile_cost_distribution_hold_v1` | Clean B-only profile outcome baseline. |
| 2 | `simple_profile_mark_to_distribution_hedge_v1` | Minimal B+C hedge ratio mechanics. |
| 3 | `simple_option_first_10s_momentum_hold_v1` | High-volume C-only event momentum baseline. |
| 4 | `simple_option_touch_reclaim_rebound_v1` | Tests buy-rebound plus SELL lifecycle. |
| 5 | `simple_retrace_ladder_cashout_rebuy_v1` | Tests cashout/rebuy lifecycle before full grid. |
| 6 | `simple_option_volatility_grid_spacing_v1` | Tests minimal grid placement and spacing. |

Second-wave candidates:

- `simple_pre_event_profile_side_start_v1`
- `simple_option_pre_event_drift_side_start_v1`
- `simple_underdog_20c_rebound_scalp_v1`
- `simple_profile_confirmed_underdog_rebound_v1`
- `simple_stale_order_review_only_v1`

Shadow-first candidates:

- `simple_final_minute_skip_resolved_v1`
- `simple_final_minute_underdog_comeback_v1`
- any candidate relying primarily on A-only crypto direction

## What Not To Overfit

Do not create more than V5 for a signal family unless a clear blocker explains each new version.

Current A-only crypto directional signals should not be forced into more directional versions just because they are close to threshold. The better pattern is:

- use A-only crypto signals as regime, avoid, volatility, or freshness context;
- use A+C or A+B+C confluence when crypto direction must affect entries;
- do not let weak A-only direction decide live exposure by itself.

Current profile and option-price signals should not be promoted directly from structural pass. They need:

- disjoint historical event replay;
- baseline comparison;
- fillability simulation using captured path/orderbook data;
- live-shadow confirmation with no trading.

## Next Implementation Step

Before any live run, convert the first six priority candidates into read-only strategy specs with:

- strategy id
- required signal ids
- entry rule
- exit/review rule
- budget cap
- allowed order types
- lifecycle coverage requirements
- expected observations
- promotion/demotion criteria

Only after those specs pass replay/shadow checks should we run a supervised minimal live validation bundle.
