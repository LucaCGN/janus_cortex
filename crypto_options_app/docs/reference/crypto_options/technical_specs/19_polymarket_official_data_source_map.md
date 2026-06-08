# Polymarket Official Data Source Map

This document is the Step 0 source map for the price-path hardening phase. It defines how the official Polymarket repositories should be used by `crypto_options_app` data services before any further Rotation9-style strategy work.

Canonical app root:

`crypto_options_app`

Canonical DB:

`crypto_options_app/data/crypto_options_data.sqlite`

## Source Repositories

| Repository | Primary Use | Data We Should Use | App Module/Table Target | Priority |
| --- | --- | --- | --- | --- |
| `Polymarket/py-clob-client-v2` | Python reference for CLOB REST endpoints. | `/book`, `/books`, `/midpoint`, `/midpoints`, `/price`, `/prices`, `/spread`, `/spreads`, `/last-trade-price`, `/last-trades-prices`, `/prices-history`, `/markets/live-activity/{condition_id}`. | `crypto_options_app/data_services/polymarket_option_price_capture.py`, `crypto_options_app/data_services/polymarket_live_activity_capture.py`; tables `polymarket_price_ticks`, `polymarket_order_books`, `polymarket_order_book_levels`, `polymarket_trade_prints`, `polymarket_updown_pair_snapshots`, `polymarket_event_path_stats`. | P0 |
| `Polymarket/real-time-data-client` | Official websocket wrapper for low-latency activity streams. | `activity.trades` and `activity.orders_matched`, filtered by `event_slug` or `market_slug`. | New websocket service after REST fixture parity: `crypto_options_app/data_services/polymarket_realtime_activity_stream.py`; table `polymarket_trade_prints`; data-quality watermark for stream freshness. | P0 |
| `Polymarket/polymarket-cli` | Operational smoke-test/reference client. | CLI behavior for books, batch books, price history, spread, midpoint, last trades, and authenticated trade commands. | Test fixtures and manual non-trading diagnostics only. Use to validate endpoint assumptions, not as runtime dependency. | P1 |
| `Polymarket/polymarket-us-python` | Secondary market metadata/book fallback for US API surfaces. | Market list/detail, BBO/book, settlement metadata where CLOB metadata is incomplete. | Optional fallback under `crypto_options_app/feeds/polymarket_events.py`; tables `events`, `event_tokens`, `event_outcomes`. | P2 |

Note: `Polymarket/py-clob-client-v2` currently points new projects toward `Polymarket/py-sdk`. This phase still maps the four reviewed repos because they are the repos already tied to our observed data gaps, but `py-sdk` should be evaluated before adding another long-lived runtime dependency.

## Current Gap Found In Rotation9 Review

The current app captures paired Up/Down price and order-book path data, but `polymarket_trade_prints` has zero rows. This means we can describe quote movement, visible depth, and paired path volatility, but we cannot yet distinguish executed market flow from passive book movement.

Required fix:

1. Use `py-clob-client-v2` REST semantics for `/markets/live-activity/{condition_id}` as a fixture-tested read-only service.
2. Add websocket capture from `real-time-data-client` for `activity.trades` and `activity.orders_matched` after REST parity works.
3. Backfill and validate local path capture with `/prices-history`.
4. Keep `polymarket-cli` as an external smoke-test oracle for endpoint behavior.
5. Use `polymarket-us-python` only when CLOB metadata is missing.

## Data Contracts

### Order Book / Quote Path

Source:

- `py-clob-client-v2` `/books` preferred for batch capture.
- `/book` accepted for single-token fallback.

Tables:

- `polymarket_price_ticks`
- `polymarket_order_books`
- `polymarket_order_book_levels`
- `polymarket_updown_pair_snapshots`

Required fields:

- provider/chart timestamp
- system receive timestamp
- insert timestamp
- source latency
- best bid/ask
- midpoint
- spread
- top depth
- normalized book levels
- event and token identity

### Trade Prints / Matched Activity

Sources:

- REST: `py-clob-client-v2` `/markets/live-activity/{condition_id}`.
- Websocket: `real-time-data-client` topic `activity`, types `trades` and `orders_matched`.

Table:

- `polymarket_trade_prints`

Required fields:

- token id
- event token key when resolvable
- trade timestamp
- price
- size
- side
- raw source payload

Data-quality requirement:

- `/health` must show `trade_print_count` and warn when price paths exist for completed events but trade prints are missing.

### Historical Price Backfill

Source:

- `py-clob-client-v2` `/prices-history`.

Use:

- Backfill local gaps.
- Validate whether local polling missed sharp path moves.
- Seed replay for periods before the standalone capture service started.

Tables:

- `polymarket_price_ticks`
- optional future `polymarket_price_history_backfills`

### Event Metadata Fallback

Source:

- `polymarket-us-python` market list/detail/book endpoints when CLOB event metadata is incomplete.

Tables:

- `events`
- `event_tokens`
- `event_outcomes`

## Event Path Statistics

Every completed captured event should produce or refresh `polymarket_event_path_stats`.

Required metrics:

- snapshot count
- first and last capture timestamp
- Up first/last/min/max/range
- interpolated Up path checkpoints at 0s, 10s, 30s, 60s, 120s, 180s, 240s, 270s, and 300s after event start
- pre-event checkpoints at -15m, -10m, -5m, -2m, -60s, -30s, and -10s before event start
- first-touch seconds for configured price levels
- path direction and path efficiency
- time to first extreme price band
- average and max distance between alternating local highs/lows
- average and max rolling 30s and 60s high-low range
- absolute Up movement per minute
- Up standard deviation
- price bucket counts
- level crossing counts
- near-50c sample count
- extreme sample count
- direction flip count
- strong rebound touch count
- Up/Down pair-sum range
- average pair depth pressure
- average and max source latency
- trade-print count

These metrics are replay and observability inputs. They do not authorize orders.

## Runtime Safety

All data-source services are read-only. They must reject these flags if present:

- `JANUS_CRYPTO_OPTIONS_LIVE_EXECUTE`
- `JANUS_CRYPTO_OPTIONS_LIVE_APPROVED`
- `JANUS_CRYPTO_OPTIONS_ACK_LIVE_RISK`

No data-source service may emit execution intents or route orders.

## Implementation Order

1. REST live-activity capture with fixture tests.
2. Persist event path statistics and expose them in `/health`.
3. Add `/prices-history` backfill service.
4. Add websocket activity stream service.
5. Use captured trade prints and path stats in replay-safe grid/scalp/cashout tests.
