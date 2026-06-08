# Data Blocks A/C Indicator Inventory

Date: 2026-06-04

Status: implementation checkpoint for data blocks A and C before the profile-distribution signal phase.

Scope:

- A: historical crypto price related indicators/stats.
- C: historical Polymarket option price related stats.
- B profile universe/order aggregation is intentionally deferred to the next phase.

No row or service in this document may authorize trading. All services are read-only and reject live execution flags.

## Readiness Checkpoint

| Block | Module | Central Tables | 30s readiness target | Current status |
| --- | --- | --- | --- | --- |
| A | `underlying_technical_observers` | `external_technical_observer_snapshots`, `external_technical_observer_components`, `data_signal_readiness_snapshots` | BTC/ETH IFCM `1m`, `5m`, `15m` plus TradersUnion current observer context | Implemented and tested. Latest live run wrote 18 snapshots, 356 components, 2 ready rows. |
| C | `polymarket_option_price_capture` | `polymarket_price_ticks`, `polymarket_order_book_levels`, `polymarket_updown_pair_snapshots`, `polymarket_event_path_stats`, `data_signal_readiness_snapshots` | BTC/ETH fresh paired Up/Down snapshot with reference prices | Implemented and tested. No-lookback current+15m pass completed in about 3s. |

## A. Crypto Price / Technical Analysis Inventory

| Indicator or stat | Source | event_phase_relevance | Time frames relevant | Refresh rate | Signal example | Logic to use in relevant signal |
| --- | --- | --- | --- | --- | --- | --- |
| Latest underlying tick | Exchange tick/book stream; `underlying_price_ticks` | both | seconds, 1m, 5m | target sub-second to 5s when websocket is running | `{symbol:"BTC", price:63520, age_s:1.2}` | Current distance from event target and latency anchor. |
| Underlying bid/ask spread | Exchange book ticker, pending `underlying_book_ticker_snapshots` | both | seconds, 1m | target sub-second to 5s | `{spread_bps:4.1}` | Skip or down-weight signals when underlying liquidity/spread is unstable. |
| OHLCV candles | Exchange REST/WebSocket candle builder; `underlying_candles` | pre,both | 1m, 5m, 15m, 1h, 1d | 1m candle close plus restart backfill | `{close:63520, volume:182.4}` | Backfill local indicators and replay-safe historical context. |
| `target_relative_ema_momentum_v1` | Local `indicator_snapshots` | both | 1m, 5m, 15m, 30m, 1h | every 30s after fresh ticks/candles | `{direction:"down", confidence:0.64}` | Identify whether price is moving toward or away from the event threshold. |
| `volume_weighted_pressure_v1` | Local tick/candle volume | live | 1m, 5m, 15m | every 30s after volume feed is fresh | `{pressure:"sell", value:-0.42}` | Estimate market-side pressure during event window. |
| `support_resistance_band_confluence_v1` | Local candles/ranges | pre,both | 5m, 15m, 1h | every 30s for short bands; slower for long bands | `{near_resistance:true, band:63650}` | Detect support/resistance or rebound zones near event threshold. |
| `volatility_per_second_5m` | Local ticks/candles | live | 5m | every 30s | `{vol_per_s:0.73}` | Determine whether 5m event can realistically cross target or sustain reversal. |
| `volatility_per_second_1h` | Local ticks/candles | pre,both | 1h | every 30s to 1m | `{vol_per_s:0.21}` | Baseline short event volatility against recent market regime. |
| `volatility_per_second_1d` | Local candles | pre | 1d | 1m to 5m | `{vol_per_s:0.08}` | Risk/sizing and broad regime context, not fast entry timing. |
| IFCM summary score | IFCM AJAX observer | both | 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w | 30s for 1m/5m/15m; 1-5m for longer | `{provider:"ifcm", interval:"5m", label:"Sell", score:-3}` | External technical-rating cross-check for trend conflict and consensus. |
| IFCM moving averages | IFCM components | both | same as IFCM | same as IFCM | `{EMA10:"SELL", SMA20:"SELL"}` | Cross-check local EMA direction and multi-interval conflicts. |
| IFCM oscillators | IFCM components | both | same as IFCM | same as IFCM | `{RSI:"NEUTRAL", MACD:"SELL"}` | Confirm momentum/overextension labels when local oscillator history is thin. |
| IFCM pivots | IFCM components | pre,both | same as IFCM | same as IFCM | `{classic:{S1:...,R1:...}}` | External support/resistance reference; never sole entry authority. |
| TradersUnion summary score | TradersUnion HTML observer | pre,both | current technical page context | 5m or slower | `{provider:"tradersunion", label:"Strong Sell", score:-7}` | Slower independent confirmation of major technical regime. |
| TradersUnion rich oscillators | TradersUnion components | pre,both | current technical page context | 5m or slower | `{ADX:"SELL", ATR:"HIGH_VOLATILITY", PPO:"SELL"}` | Complementary oscillator set when local or IFCM labels are incomplete. |
| TradersUnion pivots | TradersUnion components | pre | current page context | 5m or slower | `{classic:{S1:...,R1:...}}` | Secondary support/resistance comparison against IFCM/local bands. |

## C. Polymarket Option Price / Path Inventory

| Indicator or stat | Source | event_phase_relevance | Time frames relevant | Refresh rate | Signal example | Logic to use in relevant signal |
| --- | --- | --- | --- | --- | --- | --- |
| Up/Down token price tick | CLOB books; `polymarket_price_ticks` | both | current event, future 15m | service pass target <=30s, observed ~3s no-lookback | `{outcome:"Up", best_ask:0.42, best_bid:0.40}` | Current executable option price and latency anchor. |
| Order-book top depth | CLOB books; `polymarket_order_book_levels` | both | current event, future 15m | same as price capture | `{side:"ask", price:0.42, size:14.2, level:1}` | Check fillability, slippage, and whether minimum order size can execute. |
| Paired Up/Down snapshot | `polymarket_updown_pair_snapshots` | both | current event, future 15m | same as price capture | `{up_ref:0.42, down_ref:0.58, pair_sum:1.00}` | Pair consistency, asymmetric liquidity, and Up/Down pressure. |
| Up/Down reference price | Readiness payload from C capture | both | current event, future 15m | same as price capture | `{up_reference_price:0.01, down_reference_price:0.99}` | Midpoint fallback to executable bid/ask near one-sided event boundaries. |
| Pair depth pressure | Derived from paired top-3 bid/ask depth | live | current event | every 30s | `{pair_depth_pressure:0.37}` | Detect side imbalance in available book liquidity. |
| Pair spread / quote quality | Derived from top bid/ask | both | current event, future 15m | every 30s | `{up_spread:0.02, down_spread:0.03}` | Block or down-weight when execution spread is too wide. |
| Event elapsed price points | `polymarket_event_path_stats` | replay,both | 0s, 10s, 30s, 60s, 120s, 180s, 240s, 270s, 300s | updated as captures accumulate | `{0s:0.49, 30s:0.61, 60s:0.83}` | Answer how fast path moved from 50c toward 0c/100c. |
| Pre-event price points | `polymarket_event_path_stats` | pre | -15m, -10m, -5m, -2m, -60s, -30s, -10s | updated as captures accumulate | `{-15m:0.50, -60s:0.57}` | Buying-ahead and pre-event drift context. |
| Path direction and efficiency | `polymarket_event_path_stats` | replay,both | full event | after samples exist/completion | `{direction:"up", efficiency:0.74}` | Distinguish clean trend from choppy/inversion-heavy event. |
| Time to first extreme | `polymarket_event_path_stats` | replay,both | full event | after samples exist/completion | `{time_to_first_extreme_s:80}` | Measure how early market priced near resolution. |
| Average/max swing distance | `polymarket_event_path_stats` | replay,both | full event | after samples exist/completion | `{avg_swing_distance:0.11, max:0.28}` | Quantify grid/scalp opportunity from local highs/lows. |
| Rolling 30s/60s range | `polymarket_event_path_stats` | live,replay | 30s, 60s | updated as captures accumulate | `{max_rolling_30s_range:0.22}` | Intrawindow volatility available to scalp or avoid. |
| Level crossing count | `polymarket_event_path_stats` | replay,both | 10c buckets / configured levels | after samples exist/completion | `{level_crossing_count:12}` | Count how often option price crossed meaningful levels. |
| Price-bucket sample counts | `polymarket_event_path_stats` | replay,both | 10c buckets | after samples exist/completion | `{near_50c_samples:18, extreme_samples:9}` | Identify sideways vs resolved vs comeback windows. |
| Rebound/touch counts | `polymarket_event_path_stats` | replay,both | full event | after samples exist/completion | `{strong_rebound_touch_count:4}` | Measure support/resistance reaction inside option price path. |
| Pair-sum deviation | `polymarket_event_path_stats` | live,replay | current/full event | updated as captures accumulate | `{pair_sum_range:0.05}` | Detect market mispricing or stale side book. |
| Trade prints | `polymarket_trade_prints` | live,replay | current/full event | pending live-activity/websocket quality | `{trade_print_count:3}` | Confirm actual matched trades; current REST capture exists but websocket is still the stronger future source. |

## Current Gaps Before Final Strategy Design

- A primary exchange websocket/book-ticker service still needs to be run continuously as the local source of truth; IFCM/TradersUnion are now integrated fallback/complementary observers, not the primary backbone.
- C trade prints are available through the live-activity capture path, but official websocket trade/order-matched capture should be prioritized for higher granularity.
- B profile order/position aggregation is the next block and must provide the 30-second `top_profiles_distribution` payload before final strategy implementation.
