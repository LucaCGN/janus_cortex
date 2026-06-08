# Crypto Options Market And Price Data Spec

Date: 2026-06-02

Status: reviewed module spec aligned to the global one-DB data system.

## 1. Purpose

The market and price module answers:

1. What crypto events and outcome tokens exist now or soon?
2. What are the latest YES/NO prices for each event token?
3. What is the local latency between provider chart time and system receive time?
4. What is the underlying BTC/ETH/SOL/XRP price path?
5. What indicators describe momentum, pressure, support/resistance, and volatility?

It is read-only market data. It never authorizes trading.

## 2. Current Implementation

Core files:

- `app/data/pipelines/crypto/options/market_data_store.py`
- `app/data/pipelines/crypto/options/price_stream_service.py`
- `app/data/pipelines/crypto/options/polymarket_event_price_service.py`
- `app/data/pipelines/crypto/options/indicators.py`
- `codex_tool/run_crypto_options_market_data.py`
- `app/api/routers/crypto_options_market_data.py`

Current legacy DB:

- `local/shared/artifacts/crypto-options-research/market-data/crypto_market_data.sqlite`

Target canonical DB:

- `local/shared/artifacts/crypto-options-research/crypto_options_data.sqlite`

## 3. Tables

Implemented tables:

- `crypto_price_ingest_runs`
- `crypto_price_ticks`
- `crypto_candles`
- `crypto_indicator_snapshots`
- `crypto_event_indicator_context`
- `polymarket_crypto_event_universe`
- `polymarket_event_price_ticks`
- `crypto_stat_backtest_runs`
- `crypto_stat_component_results`
- `crypto_market_data_watermarks`

Implemented views:

- `v_crypto_options_latest_price_ticks`
- `v_crypto_options_latest_indicator_snapshots`
- `v_crypto_options_latest_polymarket_event_prices`

## 4. Event Universe

`polymarket_crypto_event_universe` is the canonical event/token universe.

It captures:

- active current events
- recently started/recently ended events for replay continuity
- future events already online before event start

This is required because profiles can buy options before the 5-minute event window starts.

## 5. Polymarket Event Price Stream

Primary source:

- Public Polymarket CLOB market WebSocket.

Captured message types:

- `book`
- `price_change`
- `last_trade_price`
- custom feature rows when enabled

Stored timing:

- `chart_timestamp_utc`: provider/message time.
- `system_received_at_utc`: local receive time.
- `system_inserted_at_utc`: local SQLite insert time.
- `source_latency_ms`: provider to local receive.
- `insert_latency_ms`: local receive to local insert.

REST book polling:

- optional
- bounded
- disabled by default
- must not compete with trading execution

Continuous operation should use long WebSocket sessions and short reconnect gaps, not constant REST rediscovery.

## 6. Underlying Price Stream

Primary implemented source:

- Binance public WebSocket trade stream.
- Binance public REST 1-minute klines.

Underlying prices must not be inferred from Polymarket odds.

Stored data:

- ticks in `crypto_price_ticks`
- candles in `crypto_candles`
- ingest runs in `crypto_price_ingest_runs`

## 7. Indicators

Implemented indicators:

- `target_relative_ema_momentum_v1`
- `volume_weighted_pressure_v1`
- `support_resistance_band_confluence_v1`
- `volatility_per_second_5m`
- `volatility_per_second_1h`
- `volatility_per_second_1d`

Indicator usage rule:

- Indicators are evidence components.
- They can inform replay, reporting, blockers, or sizing after validation.
- They must not directly authorize orders.

## 8. Current Smoke Results

Latest reviewed smoke:

- Event universe: 176 BTC/ETH event-token rows.
- Event range: current/recent/future 5-minute events.
- WebSocket capture: 1,834 Polymarket YES/NO price rows in a 30-second capture.
- Stored latency fields: source latency and insert latency present.
- Buying-ahead link: one profile detected after clean recompute.
- Focused tests: 6 passed.

## 9. Integration Contract

The market module provides canonical rows to:

- profile timing link enrichment
- replay event frame generation
- strategy context readers
- data quality dashboards

It must publish:

- fresh event universe
- latest event prices
- underlying price snapshots
- indicator snapshots
- service watermarks

## 10. Gaps

Remaining work:

1. Move default DB from legacy market shard to canonical DB after migration.
2. Add continuous service management around Polymarket WebSocket capture.
3. Add rollups for 5s, 15s, 1m, 5m event-price volatility.
4. Add DB-backed component backtests over indicator and event price rows.
5. Add global data status endpoint over market, profile, and replay watermarks.
