# Price Path Data Service Spec

Goal: continuously capture Polymarket crypto Up/Down option price paths with enough granularity to replay entry, exit, cashout, rebuy, stale-order review, and hedge-ratio mechanics.

## Service Boundary

The service is read-only. It must never accept or forward live trading flags. If any of these are enabled, it exits with `data_service_live_flags_rejected`:

- `JANUS_CRYPTO_OPTIONS_LIVE_EXECUTE`
- `JANUS_CRYPTO_OPTIONS_LIVE_APPROVED`
- `JANUS_CRYPTO_OPTIONS_ACK_LIVE_RISK`

Entry point:

`crypto_options_app/scripts/run_crypto_options_option_price_capture.py`

Runtime module:

`crypto_options_app/data_services/polymarket_option_price_capture.py`

Official source map:

`crypto_options_app/docs/reference/crypto_options/technical_specs/19_polymarket_official_data_source_map.md`

Trade/live activity entry point:

`crypto_options_app/scripts/run_crypto_options_market_activity_capture.py`

Trade/live activity runtime module:

`crypto_options_app/data_services/polymarket_live_activity_capture.py`

## Capture Scope

- Symbols: BTC and ETH initially.
- Event cadence: 5 minute Polymarket Up/Down markets.
- Live signal capture window: current plus future events at least 15 minutes ahead.
- Default live signal lookback: 0 minutes, to avoid just-expired event-token 404s and keep the service inside the 30-second cadence.
- Historical/path continuity is accumulated from prior captures and completed-event path-stat refreshes, not from repeatedly refetching expired markets during signal cadence.
- Data captured per token:
  - executable best bid/ask
  - midpoint
  - spread
  - top-depth sizes
  - normalized order-book levels
  - source timestamp, system receive timestamp, insert timestamp, and latency
- Data captured per event pair:
  - Up/Down paired snapshot
  - Up/Down depth imbalance
  - source latency

## Canonical Tables

- `polymarket_price_ticks`
- `polymarket_order_books`
- `polymarket_order_book_levels`
- `polymarket_trade_prints`
- `polymarket_updown_pair_snapshots`
- `polymarket_event_path_stats`
- `data_signal_readiness_snapshots`
- `data_service_watermarks`

## Indicators Produced From Price Paths

- `option_updown_pair_divergence_v1`
- `option_orderbook_depth_pressure_v1`
- `option_pair_volatility_per_second_5m_v1`
- `pre_event_option_price_drift_15m_v1`

Event-path statistics must also be persisted for completed events:

- interpolated Up price at elapsed event offsets: 0s, 10s, 30s, 60s, 120s, 180s, 240s, 270s, 300s
- pre-event Up price checkpoints: -15m, -10m, -5m, -2m, -60s, -30s, -10s
- path direction and path efficiency, where efficiency is `abs(last-first) / sum(abs(delta))`
- time to first extreme, defined as first sample at or below 10c or at or above 90c
- average and max swing distance between alternating local highs/lows
- average and max rolling 30s and 60s high-low ranges
- volatility / absolute movement per minute
- price-level reach and crossing counts
- 10c price-bucket sample counts
- rebound/touch counts
- direction flip counts
- Up/Down pair-sum deviation
- depth-pressure imbalance
- latency summaries
- trade-print count

These indicators are context inputs only; they do not authorize orders.

## Official Polymarket Sources

- `Polymarket/py-clob-client-v2`: canonical REST reference for books, batch books, prices, spreads, last trades, price history, and `/markets/live-activity/{condition_id}`.
- `Polymarket/real-time-data-client`: canonical websocket reference for `activity.trades` and `activity.orders_matched`.
- `Polymarket/polymarket-cli`: operational smoke-test reference for endpoint behavior.
- `Polymarket/polymarket-us-python`: secondary market metadata/book fallback.

## Health Requirements

`/health` must expose:

- centralized DB path
- artifact root
- current data-service watermarks
- row counts for price ticks, books, book levels, pair snapshots, and indicators
- latest completed event-path statistics
- Block C data-signal readiness rows from `data_signal_readiness_snapshots`
- trade-print count and warning state when price paths exist but trade prints are missing
- stale or failed data-service state

Current 30-second readiness rule for Block C:

- BTC and ETH must have a fresh `polymarket_updown_pair_snapshots` row.
- The latest pair payload must include a usable reference price for both Up and Down. Reference price is midpoint when available, falling back to executable ask/bid when a one-sided book makes midpoint null near event boundaries.
- Partial provider 404s for future/expired tokens are stored as payload warnings, not readiness blockers, when the current symbol has a fresh paired snapshot.
- Readiness rows are written as `data_block='C'`, `module_id='polymarket_option_price_capture'`.

Validated centralized run on 2026-06-04:

- No-lookback current+15m capture completed in about 3 seconds.
- 20 option ticks, 360 order-book level rows, 10 paired Up/Down snapshots, and 10 event-path stat refreshes were persisted in one healthy pass.
- 2 Block C readiness rows were written.

## Test Gates

- Price capture writes ticks, depth rows, pair snapshots, and watermarks.
- Price capture writes Block C readiness rows.
- Live trading flags are rejected.
- Replay can consume at least one captured or fixture Up/Down path with no lookahead.
- Trade/live-activity capture writes fixture trade prints and watermarks.
- Completed event-path statistics are visible in `/health`.
