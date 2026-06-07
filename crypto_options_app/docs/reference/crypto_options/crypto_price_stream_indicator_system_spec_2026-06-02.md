# Crypto Price Stream And Indicator System Spec

Date: 2026-06-02

Status: implemented baseline for the second data-infrastructure block.

Review note:

- Superseded for architecture planning by `crypto_options_global_data_system_spec_2026-06-02.md` and `crypto_options_market_price_data_spec_2026-06-02.md`.
- This document remains useful as implementation history, but its separate-market-data-DB wording is now a legacy shard description, not the target architecture.

Implementation snapshot:

- Store: `app/data/pipelines/crypto/options/market_data_store.py`
- Stream/fetch service: `app/data/pipelines/crypto/options/price_stream_service.py`
- Polymarket event price service: `app/data/pipelines/crypto/options/polymarket_event_price_service.py`
- Indicator engine: `app/data/pipelines/crypto/options/indicators.py`
- CLI: `codex_tool/run_crypto_options_market_data.py`
- API router: `app/api/routers/crypto_options_market_data.py`
- DB: `local/shared/artifacts/crypto-options-research/market-data/crypto_market_data.sqlite`

Smoke test on 2026-06-02:

- Binance public WebSocket trade stream captured and persisted 12 BTC/ETH trade ticks.
- Binance public REST kline fetch persisted 360 one-minute candles: 180 BTC and 180 ETH.
- Local indicator compute persisted 6 latest snapshots: 3 indicators x 2 symbols.
- All focused tests passed.

Polymarket event-price block:

- Discovers current and future BTC/ETH recurring Up/Down event tokens.
- Captures public CLOB market-channel WebSocket rows for YES/NO token prices.
- Persists provider/chart time, system receive time, system insert time, source latency, insert latency, spread, top-of-book sizes, trade prints, and per-token price deltas.
- Links profile activity to event start/end windows and flags `buying_ahead` when a profile buys before event start.
- Uses WebSocket as the primary source. REST `/book` polling is optional, bounded, and disabled by default so it does not compete with trading.

This document defines the crypto underlying price infrastructure that should sit beside the profile universe store. It is read-only market-data infrastructure. It does not place, cancel, sign, broadcast, route, recommend, or authorize orders.

## 1. Purpose

The crypto options system has three data infrastructure blocks:

1. Profile universe, grading, and profile-derived signal generators.
2. Replay engine for reporting and more effective backtesting.
3. Crypto underlying price stream, historical candles, indicators, and statistical signals.

Block 1 is now implemented in the legacy profile shard and targeted for migration into the canonical crypto options DB. Block 2 will wait until the trading engine is reorganized. This spec covers Block 3 as implementation history; the active architecture target is the one-DB global data spec.

The price/indicator system exists to answer:

1. What is the current BTC/ETH/SOL/XRP underlying price?
2. What was the high-granularity recent price path?
3. Is the current price behavior supportive of Up, Down, hold, or block decisions relative to an event threshold?
4. Did an indicator component actually confirm a support/resistance/momentum hypothesis in the 5 minute event scope?

## 2. Existing Repo Surface

Current useful files:

- `app/data/nodes/crypto/candles.py`
- `app/data/nodes/crypto/reference.py`
- `app/data/pipelines/crypto/options/indicators.py`
- `tests/app/data/pipelines/crypto/options/test_indicators_pytest.py`

Existing capabilities:

- Normalize candle rows.
- Fetch public Binance klines through `BinanceKlineCandleProvider`.
- Normalize decoded reference price reports.
- Compute simple momentum, moving-average, volatility, RSI, threshold distance, and time-to-close features.

Current gaps:

- No durable high-granularity price database.
- No continuously running price stream service.
- No 3 month retention/watermark model.
- No multi-interval candle rollup.
- No event-threshold-relative indicator signal table.
- No statistical component backtest engine.
- No API stream exposing latest price/indicator/event-context snapshots.

## 3. Design Principles

- Never infer underlying BTC/ETH prices from Polymarket odds.
- Store raw price observations separately from profile/store data.
- Use idempotent upserts and watermarks.
- Keep only 3 months of market data by default.
- Compute indicators from exchange-backed candles/ticks.
- Treat indicators as evidence components, not execution permission.
- Keep indicator signals explainable and individually backtestable.
- Make backtests component-level, not only strategy-level.
- Allow later provider expansion without changing downstream consumers.

## 4. Initial Indicator Set

We need indicators that can become useful after only 1-2 hours of collected history, because the next trading test may start before we have a long local history.

### 4.1 Target-Relative EMA Momentum

Indicator ID: `target_relative_ema_momentum_v1`

Purpose:

- Detect directional pressure relative to the event threshold price.
- Answer whether the underlying is moving in favor of Up or Down before event close.

Inputs:

- 1 minute candles.
- Event threshold price.
- Event end time.
- Current underlying price.

Windows:

- EMA fast: 5 x 1m.
- EMA slow: 15 x 1m.
- Slope lookback: 3 x 1m.
- Optional 5m aggregate confirmation.

Computed fields:

- `ema_fast`
- `ema_slow`
- `ema_spread = ema_fast - ema_slow`
- `ema_fast_slope`
- `ema_slow_slope`
- `target_delta_abs = abs(current_price - event_threshold_price)`
- `target_delta_signed_up = current_price - event_threshold_price`
- `target_delta_signed_down = event_threshold_price - current_price`
- `momentum_direction`: `up`, `down`, or `neutral`
- `momentum_confidence`: 0-1

Signal examples:

- Up-favorable: price above threshold, EMA fast above slow, fast slope positive.
- Down-favorable: price below threshold, EMA fast below slow, fast slope negative.
- Contra-threshold warning: price is near threshold but EMA momentum is against the side being considered.

Why first:

- It can be computed with useful stability after roughly 15-30 minutes of 1m data.
- It directly maps to Up/Down event logic.

### 4.2 Volume-Weighted Pressure

Indicator ID: `volume_weighted_pressure_v1`

Purpose:

- Estimate buy/sell pressure and whether movement has meaningful volume behind it.
- Avoid treating low-volume noise as directional confirmation.

Inputs:

- 1 minute candles with volume.
- Event threshold price.

Windows:

- Rolling VWAP: 15 x 1m.
- Volume z-score: 30 x 1m.
- Candle close-location value: each 1m candle.

Computed fields:

- `rolling_vwap_15m`
- `price_vs_vwap = current_price - rolling_vwap_15m`
- `volume_zscore_30m`
- `close_location_value = (close - low) / (high - low)`
- `pressure_direction`: `buy_pressure`, `sell_pressure`, or `neutral`
- `pressure_confidence`: 0-1

Signal examples:

- Sell pressure: current price below VWAP, close-location low in candle range, elevated volume.
- Buy pressure: current price above VWAP, close-location high in candle range, elevated volume.
- Weak pressure: volume z-score low, price near VWAP, or candle closes near midpoint.

Important limitation:

- Public candle volume is not full order-flow imbalance. This is a proxy. It should be backtested as pressure metadata, not treated as true exchange-level bid/ask flow.

Why first:

- It adds volume context to momentum using only 30-60 minutes of candles.
- It helps distinguish "fast move with participation" from "thin late noise."

### 4.3 Support/Resistance Band Confluence

Indicator ID: `support_resistance_band_confluence_v1`

Purpose:

- Detect whether price is approaching, rejecting, or breaking a support/resistance area relative to the event threshold.
- Provide the "105 resistance band while event threshold is 100" style signal.

Inputs:

- 1 minute candles.
- Event threshold price.
- Current price.

Windows:

- Bollinger middle: 20 x 1m simple moving average.
- Bollinger upper/lower: 2 standard deviations.
- Donchian high/low: 30 x 1m.
- Optional 60 x 1m once at least 1 hour exists.

Computed fields:

- `bb_mid_20m`
- `bb_upper_20m`
- `bb_lower_20m`
- `bb_width_20m`
- `donchian_high_30m`
- `donchian_low_30m`
- `nearest_resistance`
- `nearest_support`
- `distance_to_resistance`
- `distance_to_support`
- `distance_threshold_to_resistance`
- `distance_threshold_to_support`
- `band_event`: `resistance_touch`, `resistance_rejection`, `support_touch`, `support_bounce`, `breakout`, `breakdown`, or `none`
- `band_confidence`: 0-1

Signal examples:

- Down confirmation: event threshold is 100, current price is under or near 105 resistance, resistance rejection occurs, EMA momentum points down, volume pressure is sell-biased.
- Up confirmation: price bounces from support, EMA momentum turns up, volume pressure is buy-biased.
- No confirmation: price is between bands and no rejection/bounce/breakout event happened.

Why first:

- It is directly tied to support/resistance hypotheses.
- It can be computed with 30-60 minutes of data.
- It can be backtested as a component independent of profile signals.

## 5. Database

Use a separate SQLite DB from both Janus app DB and profile store:

- `local/shared/artifacts/crypto-options-research/market-data/crypto_market_data.sqlite`

### 5.1 `crypto_price_ingest_runs`

Records every streaming/fetch run.

Columns:

- `run_id` primary key
- `started_at_utc`
- `completed_at_utc`
- `status`
- `source`
- `symbols_json`
- `fetch_interval_seconds`
- `max_concurrency`
- `rows_observed`
- `rows_inserted`
- `error_count`
- `summary_json`

### 5.2 `crypto_price_ticks`

Durable high-granularity observations.

Columns:

- `tick_key` primary key
- `symbol`
- `source`
- `observed_at_utc`
- `exchange_timestamp_utc`
- `price`
- `bid`
- `ask`
- `last_size`
- `volume_24h`
- `raw_json`
- `inserted_at_utc`

Indexes:

- `(symbol, observed_at_utc)`
- `(source, symbol, observed_at_utc)`

Retention:

- Delete rows older than 90 days unless pinned by a backtest artifact.

### 5.3 `crypto_candles`

Normalized OHLCV candles. This should support provider candles and local rollups.

Columns:

- `candle_key` primary key
- `symbol`
- `source`
- `interval`
- `opened_at_utc`
- `closed_at_utc`
- `open`
- `high`
- `low`
- `close`
- `volume`
- `trade_count`
- `source_tick_count`
- `raw_json`
- `inserted_at_utc`
- `updated_at_utc`

Intervals:

- `1s` optional if provider/tick quality supports it.
- `5s`
- `15s`
- `1m`
- `5m`
- `15m`

Initial minimum:

- `1m`, `5m`, `15m`.

### 5.4 `crypto_indicator_snapshots`

Indicator values at each candle close.

Columns:

- `indicator_snapshot_key` primary key
- `symbol`
- `source`
- `interval`
- `indicator_id`
- `computed_at_utc`
- `window_start_utc`
- `window_end_utc`
- `price`
- `value_1`
- `value_2`
- `value_3`
- `direction`
- `confidence`
- `components_json`
- `quality_flags_json`
- `inserted_at_utc`

Examples:

- `target_relative_ema_momentum_v1`
- `volume_weighted_pressure_v1`
- `support_resistance_band_confluence_v1`

### 5.5 `crypto_event_indicator_context`

Event-threshold-relative context snapshots.

Columns:

- `event_context_key` primary key
- `event_slug`
- `condition_id`
- `symbol`
- `event_threshold_price`
- `event_end_time_utc`
- `computed_at_utc`
- `current_price`
- `time_to_event_end_seconds`
- `target_delta_abs`
- `target_delta_signed_up`
- `target_delta_signed_down`
- `momentum_direction`
- `momentum_confidence`
- `pressure_direction`
- `pressure_confidence`
- `band_event`
- `band_confidence`
- `combined_direction`
- `combined_confidence`
- `blockers_json`
- `components_json`
- `quality_flags_json`

This is the table the trading system should read when it needs underlying context for a candidate event.

### 5.6 `crypto_stat_backtest_runs`

Pure statistical backtest runs.

Columns:

- `backtest_run_id` primary key
- `started_at_utc`
- `completed_at_utc`
- `status`
- `dataset_start_utc`
- `dataset_end_utc`
- `symbols_json`
- `component_ids_json`
- `event_count`
- `signal_count`
- `summary_json`

### 5.7 `crypto_stat_component_results`

Backtest results by component and hypothesis.

Columns:

- `result_key` primary key
- `backtest_run_id`
- `component_id`
- `symbol`
- `interval`
- `test_label`
- `sample_count`
- `hit_count`
- `miss_count`
- `hit_rate`
- `precision`
- `recall`
- `avg_forward_return`
- `median_forward_return`
- `max_adverse_excursion`
- `max_favorable_excursion`
- `threshold_cross_rate`
- `support_resistance_confirm_rate`
- `summary_json`

### 5.8 `crypto_market_data_watermarks`

Service watermarks.

Columns:

- `service_name` primary key
- `symbol`
- `source`
- `last_observed_at_utc`
- `last_candle_opened_at_utc`
- `last_indicator_computed_at_utc`
- `status`
- `state_json`
- `updated_at_utc`

### 5.9 `polymarket_crypto_event_universe`

Token-level universe for recurring crypto Up/Down events, including future events that are online before their trading window starts.

Columns:

- `event_token_key` primary key
- `event_id`
- `event_slug`
- `market_id`
- `condition_id`
- `market_slug`
- `symbol`
- `cadence_seconds`
- `outcome`
- `token_id`
- `event_start_time_utc`
- `event_end_time_utc`
- `settlement_threshold`
- `active`
- `closed`
- `source`
- `raw_json`
- `updated_at_utc`

This is token-level because YES and NO are separate CLOB assets with separate orderbooks and price streams.

### 5.10 `polymarket_event_price_ticks`

Real-time YES/NO event price observations from the public Polymarket market WebSocket and optional bounded book polling.

Key fields:

- Event identity: `event_id`, `event_slug`, `market_id`, `condition_id`, `market_slug`, `symbol`, `outcome`, `token_id`
- Event window: `event_start_time_utc`, `event_end_time_utc`
- Feed time: `chart_timestamp_utc`, `system_received_at_utc`, `system_inserted_at_utc`
- Latency: `source_latency_ms`, `insert_latency_ms`
- Quote/trade: `mid_price`, `best_bid`, `best_ask`, `spread`, `trade_price`, `trade_size`, `side`
- Depth: `bid_size`, `ask_size`, `depth_top3_bid_size`, `depth_top3_ask_size`
- Fluctuation: `mid_price_delta`, `best_bid_delta`, `best_ask_delta`, `trade_price_delta`
- `raw_json`

`chart_timestamp_utc` is the provider/message timestamp when available. `system_received_at_utc` is when this process received the message. Their difference is the primary feed-latency measurement.

### 5.11 Profile/Event Timing Link

The profile store now links raw profile activity to the Polymarket event universe:

- `profile_raw_activity.event_start_time_utc`
- `profile_raw_activity.event_end_time_utc`
- `profile_raw_activity.seconds_before_event_start`
- `profile_raw_activity.buying_ahead`
- `profile_raw_activity.active_during_event`

The profile store also has `profile_event_timing_links` and `v_crypto_options_profile_buying_ahead`.

This isolates profiles that buy future events before event start. Those accounts should be analyzed as a distinct signal behavior rather than blended with live in-window trading signals.

## 6. Pipeline

### 6.1 Price Stream Service

Goal:

- Continuously capture underlying prices and/or provider candles for BTC, ETH, SOL, and XRP.

Initial source:

- Public Binance market-data klines through the existing `BinanceKlineCandleProvider`.

Future sources:

- Chainlink Data Streams for settlement/reference reports.
- Additional exchange data provider if latency/coverage requires it.

Runtime:

- Async queue by symbol.
- Bounded semaphore for provider calls.
- Per-source rate-limit config.
- Idempotent SQLite upserts.
- 90 day retention.

Minimum loop:

1. Fetch latest 1m candles for configured symbols.
2. Upsert into `crypto_candles`.
3. Roll up into 5m and 15m candles.
4. Compute indicators.
5. Publish latest indicator/event-context rows.
6. Update watermarks.

Optional high-granularity loop:

1. Fetch latest ticker price every 1-5 seconds.
2. Upsert into `crypto_price_ticks`.
3. Roll ticks into 5s, 15s, and 1m candles.

### 6.2 Historical Backfill

Target:

- Maintain only 3 months of local market history.

Initial behavior:

- Start streaming now and build proprietary history forward.
- Backfill from public candle provider only when needed and rate limits allow it.

Backfill priority:

1. Last 2 hours for immediate indicators.
2. Last 24 hours for sanity/replay.
3. Last 7 days for first component backtests.
4. Up to 90 days once service is stable.

### 6.3 Indicator Service

Runs after candle upserts.

For each symbol and interval:

1. Load last required lookback window.
2. Compute indicator snapshots.
3. Upsert into `crypto_indicator_snapshots`.
4. For active crypto option events, compute `crypto_event_indicator_context`.

Initial required lookbacks:

- EMA momentum: at least 15 candles; preferred 30.
- Volume pressure: at least 30 candles; preferred 60.
- Band confluence: at least 30 candles; preferred 60.

### 6.4 Event Context Builder

Inputs:

- Active Polymarket event metadata.
- Event threshold price.
- Event end time.
- Current underlying price.
- Latest indicator snapshots.

Outputs:

- `target_delta_abs`
- `target_delta_signed_up`
- `target_delta_signed_down`
- combined indicator context
- component signals
- blockers

Example context interpretation:

- If event is Up/Down from 100, current price is near 105 resistance, resistance rejection is present, EMA momentum is down, and volume pressure is sell-biased, then the event context emits a Down-favorable statistical signal.

## 7. Statistical Backtest Engine

This engine is not the full trading replay engine. It tests statistical components only.

### 7.1 Labels

Outcome labels:

- `event_up_won`: event close/reference price above threshold.
- `event_down_won`: event close/reference price below threshold.

Component labels:

- `momentum_follow_through_5m`: after signal, price moved in signal direction within 5 minutes.
- `resistance_rejection_confirmed_5m`: after resistance touch/rejection signal, price failed to break resistance and closed lower within the test horizon.
- `support_bounce_confirmed_5m`: after support touch/bounce signal, price failed to break support and closed higher within the test horizon.
- `threshold_cross_confirmed`: signal direction matched crossing behavior around event threshold.
- `pressure_follow_through_5m`: volume pressure matched forward move direction.

### 7.2 Backtest Modes

Mode 1: component-only.

- Test one indicator against its specific label.
- Example: did `support_resistance_band_confluence_v1` resistance rejection confirm in the 5 minute scope?

Mode 2: combined statistical signal.

- Combine EMA momentum, volume pressure, and band confluence.
- Test Up/Down hit rate against event outcome.

Mode 3: profile-plus-statistical overlay.

- Later only.
- Test whether profile signals improve when filtered or weighted by indicator context.

### 7.3 Metrics

For each component:

- sample count
- hit rate
- precision
- recall
- average forward return
- median forward return
- max favorable excursion
- max adverse excursion
- support/resistance confirmation rate
- threshold cross rate
- false positive rate
- time-to-confirmation

Critical rule:

- Backtests must report component outcomes separately from combined strategy outcomes. We need to know whether a band signal is useful even if the full strategy is not.

## 8. API And Integration

Read-only FastAPI endpoints should be added under:

- `/v1/crypto-options/market-data/status`
- `/v1/crypto-options/market-data/prices/latest`
- `/v1/crypto-options/market-data/candles`
- `/v1/crypto-options/market-data/indicators/latest`
- `/v1/crypto-options/market-data/event-context`
- `/v1/crypto-options/market-data/statistical-signals`
- `/v1/crypto-options/market-data/backtests`

Every response must include:

- `orders_allowed=false`
- `live_trading_authorized=false`

Trading-system integration:

- The trading system should read `crypto_event_indicator_context`.
- The integration should expose indicators as context, blockers, or sizing modifiers.
- It must not allow indicators to directly authorize orders.

Recommended signal payload:

```json
{
  "schema_version": "crypto_event_indicator_context_v1",
  "event_slug": "btc-updown-5m-example",
  "symbol": "BTC",
  "event_threshold_price": 100.0,
  "current_price": 104.7,
  "time_to_event_end_seconds": 210,
  "target_delta_signed_up": 4.7,
  "target_delta_signed_down": -4.7,
  "components": {
    "target_relative_ema_momentum_v1": {
      "direction": "down",
      "confidence": 0.72
    },
    "volume_weighted_pressure_v1": {
      "direction": "sell_pressure",
      "confidence": 0.66
    },
    "support_resistance_band_confluence_v1": {
      "band_event": "resistance_rejection",
      "nearest_resistance": 105.0,
      "confidence": 0.78
    }
  },
  "combined_direction": "down",
  "combined_confidence": 0.72,
  "orders_allowed": false,
  "live_trading_authorized": false
}
```

## 9. First Implementation Milestones

### Milestone A: Schema And Store

Implement:

- `app/data/pipelines/crypto/options/market_data_store.py`
- `crypto_market_data.sqlite`
- schema init
- summary helper
- tests for schema and idempotent upserts

### Milestone B: Price Stream Service

Implement:

- `app/data/pipelines/crypto/options/price_stream_service.py`
- async symbol queue
- bounded semaphore
- Binance 1m candle fetch
- local candle upsert
- watermarks
- CLI command:
  - `codex_tool/run_crypto_options_market_data.py fetch-latest`
  - `codex_tool/run_crypto_options_market_data.py stream`

### Milestone C: Indicator Engine

Implement:

- expanded `indicators.py`
- indicator snapshot persistence
- event context builder
- tests for 1h warmup and threshold-relative values

### Milestone D: API

Implement:

- `app/api/routers/crypto_options_market_data.py`
- read-only endpoints
- tests for latest price, indicators, event context

### Milestone E: Statistical Backtest

Implement:

- `app/data/pipelines/crypto/options/statistical_indicator_backtest.py`
- component labels
- component result table
- tests using synthetic candle/event data

## 10. Minimum Acceptance Criteria

Before this block is considered ready:

- The DB initializes independently of the profile store.
- Latest BTC/ETH candles can be fetched and persisted.
- 1m, 5m, and 15m candles are queryable.
- All three initial indicators compute from 1-2 hours of local data.
- Event context can be produced for an event threshold.
- Component-level statistical backtest can test at least:
  - EMA momentum follow-through.
  - Support/resistance confirmation.
- API endpoints return read-only payloads.
- No code path authorizes or places orders.

## 11. Open Decisions

1. Whether to capture tick-level prices now or only 1m provider candles first.
2. Whether to backfill immediately beyond 2 hours or build forward first.
3. Whether Chainlink reference reports should be required for event settlement labels before statistical backtests run.
4. Whether SOL/XRP should be included in the first pass or deferred behind BTC/ETH.
5. Whether indicator context should block entries directly or only report evidence until we have component backtest results.

Recommended initial stance:

- Start with BTC/ETH.
- Fetch and persist 1m candles first.
- Compute 5m and 15m rollups locally.
- Use EMA momentum and support/resistance confluence as first live-context indicators.
- Add volume-weighted pressure once candle volume quality is confirmed.
- Treat all outputs as research/statistical context until component backtests prove usefulness.

## 12. Implemented Baseline

Implemented schema version:

- `crypto_options_market_data_store_v1`

Implemented files:

- `app/data/pipelines/crypto/options/market_data_store.py`
- `app/data/pipelines/crypto/options/price_stream_service.py`
- `codex_tool/run_crypto_options_market_data.py`
- `app/api/routers/crypto_options_market_data.py`

Implemented tables:

- `crypto_price_ingest_runs`
- `crypto_price_ticks`
- `crypto_candles`
- `crypto_indicator_snapshots`
- `crypto_event_indicator_context`
- `crypto_stat_backtest_runs`
- `crypto_stat_component_results`
- `crypto_market_data_watermarks`

Implemented views:

- `v_crypto_options_latest_price_ticks`
- `v_crypto_options_latest_indicator_snapshots`

Implemented indicators:

- `target_relative_ema_momentum_v1`
- `volume_weighted_pressure_v1`
- `support_resistance_band_confluence_v1`
- `volatility_per_second_5m`
- `volatility_per_second_1h`
- `volatility_per_second_1d`

Current API:

- `GET /v1/crypto-options/market-data/status`
- `GET /v1/crypto-options/market-data/prices/latest`
- `GET /v1/crypto-options/market-data/indicators/latest`
- `GET /v1/crypto-options/market-data/polymarket/events`
- `GET /v1/crypto-options/market-data/polymarket/prices/latest`
- `GET /v1/crypto-options/market-data/profiles/buying-ahead`

Current CLI:

```powershell
python codex_tool\run_crypto_options_market_data.py init
python codex_tool\run_crypto_options_market_data.py capture-stream --symbols BTC ETH --max-messages 20 --timeout-seconds 20
python codex_tool\run_crypto_options_market_data.py fetch-klines --symbols BTC ETH --lookback-minutes 180 --interval 1m
python codex_tool\run_crypto_options_market_data.py compute-indicators --symbols BTC ETH --lookback-minutes 180 --interval 1m
python codex_tool\run_crypto_options_market_data.py discover-polymarket-events --symbols BTC ETH --lookback-minutes 30 --lookahead-minutes 180
python codex_tool\run_crypto_options_market_data.py capture-polymarket-events --symbols BTC ETH --seconds 30 --max-tokens 120
python codex_tool\run_crypto_options_market_data.py latest-event-prices --symbols BTC ETH --limit 50
python codex_tool\run_crypto_options_market_data.py buying-ahead-profiles --limit 100
python codex_tool\run_crypto_options_market_data.py run-loop --symbols BTC ETH --max-iterations 1
python codex_tool\run_crypto_options_market_data.py run-loop --symbols BTC ETH --max-iterations 0
python codex_tool\run_crypto_options_market_data.py run-polymarket-loop --symbols BTC ETH --max-iterations 0
python codex_tool\run_crypto_options_market_data.py summary
```

All API and CLI payloads are read-only and include:

- `orders_allowed: false`
- `live_trading_authorized: false`

## 13. Backtest Data Requirements

Different tests need different warmup sizes. The system can compute features quickly, but performance claims need enough events.

| Test type | Minimum data | Preferred data | Why |
| --- | ---: | ---: | --- |
| Stream plumbing smoke test | 10-20 ticks | 1-5 minutes | Confirms WebSocket, parsing, and DB writes only. |
| EMA momentum feature availability | 15 minutes | 30-60 minutes | Needs 5m/15m EMA and short slope stability. |
| Bollinger/Donchian support-resistance availability | 30 minutes | 60-120 minutes | Needs 20m Bollinger and 30m Donchian windows. |
| VWAP/volume pressure availability | 30 minutes | 60-120 minutes | Needs volume z-score and VWAP baseline. |
| 5m volatility component | 5-10 minutes | 60 minutes | Can compute fast, but calibration improves with more samples. |
| 1h volatility component | 60 minutes | 24 hours | Needs a full 60-candle rolling window. |
| 1d volatility component | 24 hours | 7 days | Needs 1440 candles plus stability across market regimes. |
| Component sanity backtest | 2 hours | 24 hours | Only validates label/feature wiring. |
| Initial intraday component backtest | 24 hours | 7 days | Enough 5m event windows to see obvious failure modes. |
| Promotion-quality component backtest | 7 days | 30-90 days | Needed before trusting hit rate, drawdown, and confidence calibration. |

Operational rule:

- We can start using the indicator context after 1-2 hours for observation and attribution.
- We should not use indicators as hard live blockers or sizing signals until at least 24 hours of local data exists.
- We should not make promotion claims until at least 7 days of BTC/ETH local history exists, and 30-90 days is preferred.

## 14. Data Source Review

### Implemented Source: Binance Public Market Data

Use:

- WebSocket combined trade stream through `wss://data-stream.binance.vision/stream?streams=...`
- REST klines through the existing `BinanceKlineCandleProvider`

Reason:

- No API key needed.
- Works for BTC/ETH immediately.
- Official WebSocket docs define combined stream payloads and the market-data-only endpoint.
- Official REST docs define exchange kline/candlestick market-data endpoints.

Observed result:

- WebSocket stream capture works from the current environment.
- REST 1m kline backfill works from the current environment.

### Implemented Source: Polymarket Public CLOB Market WebSocket

Use:

- Public market channel at `wss://ws-subscriptions-clob.polymarket.com/ws/market`.
- Subscribe with event token IDs and type `market`.
- Capture `price_change`, book, trade, and custom market rows when provided.

Reason:

- It is the lowest-latency public source available in the repo for YES/NO event-token prices.
- It avoids REST polling pressure while trading services are running.
- It provides provider timestamps where available, which lets us compare chart time to system receive time.

Operational constraints:

- Subscribe only to current, recent, and near-future crypto Up/Down event tokens.
- Keep REST book polling disabled by default.
- Use the event universe to capture future events before start so pre-event profile buys can be flagged.
- Keep continuous capture sessions long-lived by default: subscribe through WebSocket for several minutes, then reconnect/rediscover with a short gap. Do not rediscover the full event universe every minute unless debugging.
- Treat the stream as read-only market data. It does not authenticate or authorize trading.

### Candidate Source: Coinalyze

Use later for:

- Open interest.
- Funding rate.
- Predicted funding.
- Long/short ratio.
- Liquidation history.
- External OHLCV comparison.

Reason:

- Coinalyze exposes derivatives context that Binance spot candles do not provide.
- It requires an API key and has a rate limit, so it should be integrated behind a provider adapter and watermark queue rather than used directly in trading code.

### Candidate Source: CoinAPI / CoinMarketCap / CoinGecko

Use later for:

- Historical coverage or vendor redundancy.
- Cross-checking local Binance data.

Reason:

- These are more useful for backfill/redundancy than for immediate no-key streaming in this repo.

### TradingView / CentralCharts References

Use:

- Indicator inspiration and visual sanity checks.

Do not use:

- As the source of truth for local strategy calculations unless an official data API is integrated.

## 15. Next Work

1. Attach `run-loop --max-iterations 0` to a cron/automation process.
2. Add candle rollups for 5m and 15m from stored 1m candles.
3. Implement `statistical_indicator_backtest.py`.
4. Add event-threshold labels from crypto options events and settlements.
5. Integrate Coinalyze as a provider adapter if an API key is available.
6. Add gap-fill watermarks for the 3 month storage target.
