# Master Hedge Grid Scalping Signal Validation Plan

Date: 2026-06-04

Status: signal taxonomy plus first shared signal-catalog contract for the `master_hedge_grid_scalping` family. Queue ownership, automation loops, and Web UI operations are defined in `25_signal_backtest_automation_and_webui_plan.md`.

## Scope

This document defines the signal research and validation layer that must exist before another live strategy implementation round. It does not define a deployable trading strategy and does not authorize live orders. The implementation must remain read-only; "live testing" in this scope means live-shadow signal evaluation only.

The goal is to break the final family into independently testable signal hypotheses named:

```text
[family]_[type]_[sources]_[variant]_v1.py
```

Example:

```text
master_hedge_grid_scalping_side_start_cryptoprice_target_relative_momentum_v1.py
```

Each signal is a read-only hypothesis. It can become a strategy parameter, a logic gate, a continuously refreshed stat/band, or a price reference for hedge/outcome mechanics only after validation.

## Source Research Summary

The current data architecture maps correctly onto Polymarket's official source surfaces:

| Source | Official capability | Use in this plan |
| --- | --- | --- |
| Gamma API | Event and market discovery, including token IDs. | Event universe and current/future 5m Up/Down markets. |
| Data API | Public profile positions, activity, trades, holder data, open interest, and leaderboards. | Profile universe and fallback/batch `top_profiles_distribution`. |
| CLOB API | Orderbook data, pricing, midpoints, spreads, price history, authenticated order lifecycle. | Option price paths, fillability, spread/depth, replay, and supervised execution boundaries. |
| CLOB market websocket | Public book snapshots, price changes, last trade prices by asset IDs. | Lowest-latency option-price/order-book path capture for Block C. |
| Real-time data websocket | Activity trades/orders matched and crypto price topics. | Preferred future profile/activity stream and independent crypto price stream. |

Official repo notes:

- `Polymarket/real-time-data-client` documents `activity.trades`, `activity.orders_matched`, and crypto price update topics.
- `Polymarket/py-clob-client-v2` is the current Python CLOB client reference, but it also recommends the newer unified SDK for new work.
- `Polymarket/rs-clob-client-v2` documents WebSocket streams for orderbooks, prices, midpoints, user order updates, and user trade executions.
- `Polymarket/polymarket-cli` is useful as an operational smoke-test map for public Data API profile positions, trades, activity, market holders, open interest, and CLOB book/price-history commands.

Design implication: the validator should use the centralized DB as the integration boundary, not call providers directly inside each signal. Provider-facing services populate A/B/C; signal scripts consume replay-safe frames.

## Data Blocks

| Block | Current role | Signal use |
| --- | --- | --- |
| A: crypto price/technical observers | Underlying BTC/ETH price, local indicators, IFCM/TradersUnion observer rows, future exchange websocket target. | Trend, target-relative momentum, support/resistance, volatility, breakout/convergence/sideways regime, first-minute expectation. |
| B: profile universe/distribution | `top_profiles_distribution` every 30s, with cost/share/count weighted variants from stream, batch, or position fallback. | Outcome prediction, hedge ratio, pre-event side start, final-minute confirmation, profile-vs-market conflict gates. |
| C: Polymarket option path | Up/Down ticks, book depth, paired snapshots, path stats, level crossings, rebound metrics, latency. | Rebound bands, grid spacing/count/type, cashout/rebuy, stale-order review, fillability, final-minute comeback probability. |

## Signal Purposes

| Purpose | Meaning |
| --- | --- |
| `strategy_parameter` | A value that changes how the future strategy is configured, such as side start, grid count, grid spacing, or target hedge ratio. |
| `logic_gate_trigger` | A condition that allows, blocks, or changes behavior, such as stale order review or rebound buy trigger. |
| `refreshing_stat` | A continuously updated signal that the future strategy can poll every 30s, such as top profile distribution or first-minute momentum. |
| `price_reference` | A support/resistance, rebound, target, or executable reference price used by mechanics. |

## Families And Types To Validate

| Type | Why it matters to `master_hedge_grid_scalping` | Likely sources | Degraded impact |
| --- | --- | --- | --- |
| `outcome_prediction` | Provides north for hedge/scalp direction and can stand alone as a pure predictor. | A, B, C, A+B, B+C, A+B+C | Critical for final directional exposure. |
| `side_start` | Decides whether to begin flat, 50/50, or one-sided before event open. | A, B, C, A+C, A+B+C | Critical because it changes initial leverage and risk. |
| `trend_regime` | Classifies breakout, convergence, sideways, or conflicting trends before dense grid mechanics. | A, A+C | High; avoids applying grid logic to the wrong regime. |
| `support_resistance` | Gives rebound/resistance bands for underlying and option prices. | A, C, A+C | Medium to critical depending on use. |
| `buy_rebound` | Tests whether touching an option price level is actually a profitable buy trigger. | C, A+C, B+C | Critical for underdog/favorite scalp entries. |
| `grid_spacing` | Sets price delta between orders. | C, A+C | Critical; too tight burns spread, too wide misses movement. |
| `grid_count` | Sets number of ladder levels within budget and market depth. | C | High; insufficient notional created prior live-test issues. |
| `grid_type` | Selects standard buy/sell ladder, paired hedge ladder, or buy-only liquidation. | C, B+C | High; wrong type changes lifecycle mechanics. |
| `hedge_ratio` | Converts profile distribution and market prices into target Up/Down exposure. | B, B+C | Critical; this is the main profile-following output. |
| `cashout_rebuy` | Validates sell-profit and rebuy-lower loops. | C | Critical for actual hedge-grid-scalp mechanics. |
| `stale_order_review` | Defines when resting orders should be replaced or canceled. | C | Critical for live mechanics and fill integrity. |
| `final_minute` | Chooses whether to hold, liquidate, or take underdog comeback exposure. | B+C, C | High; final minute has asymmetric payoff and high risk. |
| `liquidity_depth` | Checks whether a signal is executable at minimum sizing. | C | Critical; no signal should pass if it cannot fill safely. |
| `latency_quality` | Ensures A/B/C timestamps are fresh and aligned. | A+B+C | Critical; stale data invalidates every signal. |

## First Batch Catalog

The first machine-readable catalog is implemented in:

```text
crypto_options_app/signals/validation/registry.py
```

It currently registers V1 hypotheses across all required signal types:

| Signal ID | Purpose | Data blocks | Validation target |
| --- | --- | --- | --- |
| `master_hedge_grid_scalping_outcome_prediction_profiles_top_distribution_cost_weighted_v1` | refreshing stat | B | Profile capital distribution vs event outcome and forward option movement. |
| `master_hedge_grid_scalping_outcome_prediction_profiles_top_distribution_shares_weighted_v1` | refreshing stat | B | Profile share exposure vs outcome/mark-to-market movement. |
| `master_hedge_grid_scalping_outcome_prediction_profiles_top_distribution_count_consensus_v1` | logic gate | B | Broad profile consensus vs whale-dominated distribution. |
| `master_hedge_grid_scalping_outcome_prediction_cryptoprice_multiframe_trend_conflict_v1` | logic gate | A | Multi-timeframe trend conflict vs first-minute/final direction. |
| `master_hedge_grid_scalping_outcome_prediction_cryptoprice_external_observer_consensus_v1` | logic gate | A | IFCM/TradersUnion observer consensus vs path direction. |
| `master_hedge_grid_scalping_outcome_prediction_optionprice_first_minute_momentum_v1` | refreshing stat | C | First-minute option repricing vs later outcome. |
| `master_hedge_grid_scalping_side_start_profiles_pre_event_distribution_v1` | strategy parameter | B | Buying-ahead profile distribution vs favorable early excursion. |
| `master_hedge_grid_scalping_side_start_cryptoprice_target_relative_momentum_v1` | strategy parameter | A | Target-relative crypto momentum vs early side-start result. |
| `master_hedge_grid_scalping_side_start_optionprice_pre_event_drift_v1` | strategy parameter | C | Pre-event option drift vs early profitable excursion. |
| `master_hedge_grid_scalping_trend_regime_cryptoprice_breakout_convergence_sideways_v1` | logic gate | A | Regime label vs choppy/trending option path. |
| `master_hedge_grid_scalping_support_resistance_cryptoprice_ifcm_pivot_distance_v1` | price reference | A | Pivot proximity vs rebound/resistance behavior. |
| `master_hedge_grid_scalping_support_resistance_optionprice_bucket_rebound_v1` | price reference | C | Option price bucket touches vs rebound probabilities. |
| `master_hedge_grid_scalping_buy_rebound_optionprice_touch_reclaim_v1` | logic gate | C | Buy level touch and reclaim vs deeper break. |
| `master_hedge_grid_scalping_grid_spacing_optionprice_rolling_volatility_depth_v1` | strategy parameter | C | Spacing vs fill/exit opportunity and spread cost. |
| `master_hedge_grid_scalping_grid_count_optionprice_depth_budget_v1` | strategy parameter | C | Ladder count vs budget/depth/minimum order notional. |
| `master_hedge_grid_scalping_grid_type_optionprice_inversion_frequency_v1` | strategy parameter | C | Grid type vs inversion-heavy or trend-efficient regimes. |
| `master_hedge_grid_scalping_hedge_ratio_profiles_distribution_cost_weighted_v1` | refreshing stat | B | Target Up/Down exposure from high-grade profile capital. |
| `master_hedge_grid_scalping_hedge_ratio_profiles_optionprice_mark_to_distribution_v1` | refreshing stat | B+C | Cost-aware profile target exposure using current option prices. |
| `master_hedge_grid_scalping_cashout_rebuy_optionprice_retrace_ladder_v1` | logic gate | C | Sell-profit then rebuy-lower mechanics vs hold baseline. |
| `master_hedge_grid_scalping_stale_order_review_optionprice_spread_latency_v1` | logic gate | C | Cancel/replace timing vs stale/unfilled/adverse orders. |
| `master_hedge_grid_scalping_final_minute_optionprice_profiles_comeback_probability_v1` | logic gate | B+C | Final-minute underdog/favorite allocation. |
| `master_hedge_grid_scalping_liquidity_depth_optionprice_top_depth_slippage_v1` | logic gate | C | Minimum order fillability and slippage control. |
| `master_hedge_grid_scalping_latency_quality_cryptoprice_optionprice_profiles_freshness_gate_v1` | logic gate | A+B+C | Universal freshness/alignment gate. |

## Validation Lifecycle

Every signal must move through the same lifecycle:

1. `last_week_backtest`
2. `last_month_backtest`
3. `random_sampling_backtest`
4. `live_shadow_test`

No signal may be promoted into strategy design unless all required phases pass or the exception is explicitly approved in the spec with a reason.

### Common Metrics

Each signal report must include:

- sample count
- hit rate, where "hit" is signal-specific
- average forward return
- max favorable excursion
- max adverse excursion
- time to target
- time to invalidation
- fillability when the signal implies execution
- blocker counts
- data freshness and source mode
- impact if degraded

### Example Win Criteria

| Type | Example hit definition |
| --- | --- |
| `side_start` | Selected side reaches +10c or configured sell target before max adverse drawdown. |
| `outcome_prediction` | Dominant side wins settlement or reaches favorable mark-to-market before invalidation. |
| `buy_rebound` | Touched level reclaims configured profit target within target seconds before breaking lower. |
| `grid_spacing` | Spacing captures fill and exit opportunities while avoiding over-dense adverse fills. |
| `hedge_ratio` | Target ratio improves exposure return or drawdown vs 50/50 or previous-ratio baseline. |
| `cashout_rebuy` | Cashout fills before reversal and rebuy refills lower often enough to beat hold baseline. |
| `stale_order_review` | Review rule lowers stale/adverse fills without blocking too many profitable fills. |
| `final_minute` | Underdog/favorite allocation beats no-action or hold baseline in matching states. |

## Architecture

### File Layout

```text
crypto_options_app/signals/validation/
  models.py
  registry.py
  runner.py                 # next
  result_store.py           # next
  variants/
    master_hedge_grid_scalping/
      master_hedge_grid_scalping_side_start_cryptoprice_target_relative_momentum_v1.py
      ...
```

### Shared Contract

Every signal script must expose a shared Pydantic-compatible contract:

```python
SPEC: SignalCandidateSpec

def evaluate(frame: SignalValidationFrame) -> SignalObservation:
    ...
```

The script must not import trading executors, read live flags, or write orders. It may only read replay frames and return observations/results.

### DB Extensions Needed Next

The current replay tables can already store component reports, but the signal lifecycle should be made explicit with these tables:

- `signal_specs`
- `signal_versions`
- `signal_queue_items`
- `signal_validation_runs`
- `signal_validation_results`
- `signal_observations`
- `signal_artifacts`

These tables store the first-batch registry rows, queue ownership and TTL state, phase results, version lineage, structured blockers, observations, review requests, and promotion state. Detailed queue semantics live in `25_signal_backtest_automation_and_webui_plan.md`.

### API/Web UI

Implemented now:

```text
GET /v1/crypto-options-app/signals/catalog
GET /v1/crypto-options-app/signals/catalog/{signal_id}
GET /v1/crypto-options-app/signals/backtests
GET /v1/crypto-options-app/signals/validation/status
GET /v1/crypto-options-app/signals/validation/results
GET /v1/crypto-options-app/signals/validation/queue
POST /v1/crypto-options-app/signals/validation/request-review
```

The dashboard renders:

- family
- type
- sources
- variant
- version
- description
- current validation phase
- last-week hit rate
- last-month hit rate
- random-sample hit rate
- live-shadow hit rate
- impact if degraded
- blockers
- queue owner
- next action

## Backtest Engine Requirements

The signal validator must consume replay-safe A/B/C frames:

- A: underlying ticks, external observer snapshots, local indicators
- B: top profile distributions and profile distribution components
- C: Up/Down pair snapshots, price ticks, book depth, event path stats, trade prints

Hard rules:

- No lookahead: all source timestamps must be at or before decision timestamp.
- Signal quality and execution feasibility are separate results.
- A signal that implies a hypothetical order must run fillability simulation against contemporaneous CLOB depth.
- Live shadow mode never sends orders and never authorizes trading.
- Each signal can fail independently without blocking unrelated signal scripts.

## First Implementation Steps

1. Persist first-batch signal specs into explicit DB tables.
2. Implement `SignalValidationFrame` from existing `ReplayFrame`.
3. Implement result store for phase results and observations.
4. Add a runner that claims one queued signal phase and one validation window.
5. Add the signal backtest Web UI and read-only validation endpoints.
6. Create the 5-minute execution worker and 15-minute design-review automation in `25_signal_backtest_automation_and_webui_plan.md`.
7. Implement seed V1 evaluators:
   - `outcome_prediction_profiles_top_distribution_cost_weighted_v1`
   - `side_start_optionprice_pre_event_drift_v1`
   - `buy_rebound_optionprice_touch_reclaim_v1`
7. Start the 5-minute automation only after the validator can run a no-lookahead fixture for all seed evaluators.

## Non-Goals

- No live trading.
- No strategy family execution.
- No order placement, cancellation, signing, broadcasting, redemption, or recommendation.
- No promotion to production without passing the validation lifecycle.

## Acceptance Criteria For This Phase

- A/B/C services remain read-only and healthy.
- The signal catalog covers all required master-family mechanics.
- Each signal has explicit purpose, source blocks, phase relevance, refresh cadence, validation target, win criteria, sample unit, and degradation impact.
- The API exposes the read-only catalog for automation and web UI.
- Next implementation issue can build the validator runner without redesigning naming or contracts.

## 2026-06-06 Current Strategy Design Focus: Hedge-Floor Volatility Harvesting

The working template for `master_hedge_grid_scalping` is no longer "pick the winner". It is volatility harvesting until a guaranteed floor exists, then spending only protected surplus on convex comeback exposure.

### Phases

1. Seed both sides.
   Buy both outcomes, place sell limits above entry, and place buy limits below entry. The first objective is inventory on both outcomes so option-price volatility can be harvested.

2. Scalp inversions.
   When one side falls cheap, buy it. When it rebounds, sell part of it. Continue until realized PnL accumulates and both outcome positions have a lower effective cost basis.

3. Lock a hedge floor.

   ```text
   payout_if_up = realized_cash + up_shares
   payout_if_down = realized_cash + down_shares
   guaranteed_floor = min(payout_if_up, payout_if_down)
   ```

   Once `guaranteed_floor > 0`, the mode changes from directional speculation to protected position improvement.

4. Enforce floor-preserving order validation.

   A buy of `q` shares at price `p` on Up changes final outcomes:

   ```text
   if_up += q * (1 - p)
   if_down -= q * p
   ```

   The order is valid only when it improves the weaker final outcome or preserves the opposite final outcome above the protected floor. This is a hard strategy-engine mechanic, not only a signal.

5. Use surplus for tail optionality.
   Buying the side that reaches `1c`, `5c`, or `10c` first is valid only with protected surplus. Required empirical tables:

   ```text
   touched 1c -> later reached 5c / 10c / 20c
   touched 5c -> later reached 10c / 20c / 30c
   touched 10c -> later reached 20c / 30c / 50c
   ```

   Split by time remaining, BTC/ETH distance to threshold, option volatility, spread/depth, and profile distribution.

### Required Signal Blocks

The key question is:

```text
Is this event likely to have enough inversions/rebounds to let a grid build a protected floor?
```

Required indicators/signals:

- `inversion_intensity`: crossings, flips, and 40/50/60c churn.
- `tail_reversal_probability`: empirical comeback rates from 1c/5c/10c touches.
- `grid_viability`: expected rebounds versus spread, depth, slippage, and quote freshness.
- `hedge_floor_state`: current guaranteed outcome floor.
- `floor_preserving_order_gate`: whether a proposed buy/sell improves or preserves the floor.
- `surplus_tail_budget`: protected profit available for convex tail exposure.

### Validation Priority

Selected signal rows with structural-only success must not be treated as strategy-ready merely because they show `PASSED`. Signals labeled with strict replay or structural alternate next actions need captured option-path replay, fillability, spread/slippage checks, forward-return baselines, and explicit promotion/revision decisions before they become hard dependencies.
