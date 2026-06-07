# Crypto Options App Data Feed Design

Date: 2026-06-02

Status: technical spec for feed workers and ingestion.

## Purpose

Define feed workers that keep the canonical DB current without mixing data ingestion with trading execution.

Feeds provide source-of-truth rows for profiles, events, prices, indicators, replay, and strategies.

## Feed Workers

| Worker | Inputs | Writes | Cadence |
| --- | --- | --- | --- |
| Profile activity feed | Polymarket profile/account APIs, curated profile refs | profiles, raw activity, fetch runs | async queue, bounded concurrency |
| Event universe feed | Polymarket Gamma/CLOB market APIs | events, event tokens, event outcomes | frequent discovery for current/future events |
| Polymarket price feed | CLOB WebSocket, optional bounded REST book fallback | YES/NO price ticks, books, trade prints | continuous WebSocket |
| Underlying price feed | exchange WebSocket and REST candles | underlying ticks and candles | continuous ticks plus candle backfill |
| Indicator feed | canonical market and underlying rows | indicator snapshots, event context | periodic compute |
| Reconstruction feed | profile activity plus event universe | profile event orders, positions, reconstructions, styles | after profile/event refresh |
| Signal feed | generator scores, event context, indicators | signal snapshots and aggregation outputs | strategy-defined intervals |
| Profile distribution feed | top graded profile activity/orders/positions | profile distribution snapshots, component rows, Block B readiness | every 30 seconds |

## Async And Rate-Limit Rules

- Use bounded queues for profile fetches and HTTP work.
- Use semaphores per provider, not one global unlimited pool.
- Keep WebSocket streams long-lived.
- REST polling must be bounded and must not compete with trading executor calls.
- Failed fetches record provider, error class, retry count, and next retry time.
- Feed workers must be restartable from DB watermarks.

## Watermarks

Each worker writes `data_service_watermarks` with:

- `service_name`
- `module_id`
- `last_run_at_utc`
- `status`
- `rows_observed`
- `rows_inserted`
- `error_count`
- `state_json`

Status values:

- `healthy`
- `warming`
- `stale`
- `degraded`
- `failed`

## Staleness Rules

Feeds must publish staleness separately by data type:

- profile activity age
- event universe age
- Polymarket event price age
- underlying price age
- indicator snapshot age
- reconstruction age
- signal aggregation age

Trading strategies decide whether staleness is a blocker or confidence reduction, but feed workers must report it.

## Pre-Event Capture

Future events must be captured before event start because profiles can buy ahead.

Event discovery must include:

- active current events
- recently ended events for replay continuity
- future events already online

Profile activity must be linked to event start/end windows to flag:

- `buying_ahead`
- `active_during_event`
- `late_event_activity`

## Acceptance Criteria

- Every feed is DB-backed and restartable.
- Feed workers do not authorize trading.
- WebSocket and REST usage are separated.
- Staleness is observable before strategies run.
- Future event capture supports buying-ahead analysis.

## P2 Centralization Update

Standalone data services now live under `crypto_options_app/data_services` with scripts under `crypto_options_app/scripts`. The Polymarket option price-path service captures current and future 5 minute Up/Down events 15 minutes ahead, writes ticks, order-book levels, paired snapshots, Block C readiness rows, and watermarks to the centralized DB, and rejects live trading flags. The live signal default uses no lookback so expired event-token 404s do not consume the 30-second cadence.

## P2 Underlying Indicator Update

The A data-service block is specified in `20_underlying_crypto_indicator_data_service_spec.md`. Exchange websocket trades/book ticker remain the primary BTC/ETH backbone; IFCM technical AJAX is the preferred external technical-rating observer; TradersUnion is a slower complementary cross-check. The centralized observer runner writes external observer snapshots, component rows, Block A readiness rows, and watermarks. External observer feeds must write attribution/freshness only and must not authorize trading.

## P2 Profile Distribution Update

The B data-service block is specified in `23_profile_distribution_signal_service_spec.md`. The profile distribution runner computes `top_profiles_distribution` every 30 seconds from canonical profile activity/order rows, reconstructed event orders, or position snapshot fallback rows. It writes `profile_distribution_snapshots`, `profile_distribution_components`, Block B readiness rows, and watermarks. Optional Polymarket Data API wallet activity/position polling is bounded by `max_concurrency` and remains read-only.
