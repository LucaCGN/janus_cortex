# Janus Cortex API Routes Proposal (Phase-Gated to v1)

## Purpose
This route plan is coupled to `app/docs/scalable_db_schema_proposal.md`.
A route can only be implemented when all required tables are active in its phase.

## API principles
- Prefix: `/v1`
- JSON only
- Idempotency key on write endpoints
- Long sync calls: `202 Accepted` + `job_run_id`
- No autonomous strategy execution in v1 (data serving only)
- Route activation requires matching `pytest` coverage for backing node methods and endpoint behavior.

## Activation matrix

### `v0.5.1` - System and registry foundation
| Method | Route | Required tables |
|---|---|---|
| GET | `/v1/health` | `ops.system_heartbeats` |
| GET | `/v1/providers` | `core.providers` |
| POST | `/v1/providers` | `core.providers` |
| GET | `/v1/modules` | `core.modules` |
| POST | `/v1/modules` | `core.modules` |
| GET | `/v1/sync-runs` | `core.sync_runs` |
| GET | `/v1/sync-runs/{sync_run_id}` | `core.sync_runs`, `core.raw_payloads` |

### `v0.5.2` - Catalog read/write base
| Method | Route | Required tables |
|---|---|---|
| GET | `/v1/event-types` | `catalog.event_types` |
| POST | `/v1/event-types` | `catalog.event_types` |
| GET | `/v1/information-profiles` | `catalog.information_profiles` |
| POST | `/v1/information-profiles` | `catalog.information_profiles` |
| POST | `/v1/events` | `catalog.events` |
| GET | `/v1/events` | `catalog.events` |
| GET | `/v1/events/{event_id}` | `catalog.events` |
| PATCH | `/v1/events/{event_id}` | `catalog.events` |

### `v0.5.3` - Event import and market graph
| Method | Route | Required tables |
|---|---|---|
| POST | `/v1/events/import-url` | `catalog.events`, `catalog.event_external_refs`, `catalog.markets`, `catalog.outcomes`, `core.sync_runs` |
| GET | `/v1/events/{event_id}/markets` | `catalog.markets` |
| GET | `/v1/markets/{market_id}` | `catalog.markets` |
| GET | `/v1/markets/{market_id}/outcomes` | `catalog.outcomes` |
| GET | `/v1/outcomes/{outcome_id}` | `catalog.outcomes` |
| GET | `/v1/outcomes/by-token/{token_id}` | `catalog.outcomes` |

### `v0.5.4` - Sync trigger routes (no strategy behavior)
| Method | Route | Required tables |
|---|---|---|
| POST | `/v1/sync/polymarket/events` | `core.sync_runs`, `catalog.events`, `catalog.event_external_refs` |
| POST | `/v1/sync/polymarket/markets` | `core.sync_runs`, `catalog.markets`, `catalog.market_external_refs`, `catalog.outcomes`, `catalog.market_state_snapshots` |
| POST | `/v1/sync/nba/schedule` | `core.sync_runs`, `nba.nba_games` |
| POST | `/v1/sync/nba/teams` | `core.sync_runs`, `nba.nba_teams`, `nba.nba_team_stats_snapshots` |
| POST | `/v1/sync/nba/players` | `core.sync_runs`, `nba.nba_player_stats_snapshots` |
| POST | `/v1/sync/nba/insights` | `core.sync_runs`, `nba.nba_team_insights` |
| POST | `/v1/sync/nba/mappings` | `core.sync_runs`, `nba.nba_game_event_links` |

### `v0.6.1` - Market data retrieval routes
| Method | Route | Required tables |
|---|---|---|
| GET | `/v1/outcomes/{outcome_id}/prices/ticks` | `market_data.outcome_price_ticks` |
| GET | `/v1/outcomes/{outcome_id}/prices/candles` | `market_data.outcome_price_candles` |
| GET | `/v1/outcomes/{outcome_id}/orderbook/latest` | `market_data.orderbook_snapshots`, `market_data.orderbook_levels` |
| GET | `/v1/outcomes/{outcome_id}/orderbook/history` | `market_data.orderbook_snapshots`, `market_data.orderbook_levels` |
| GET | `/v1/markets/{market_id}/state/latest` | `catalog.market_state_snapshots` |
| GET | `/v1/events/{event_id}/odds/latest` | `catalog.outcomes`, `market_data.outcome_price_ticks` |

### `v0.6.2` - Portfolio read routes
| Method | Route | Required tables |
|---|---|---|
| GET | `/v1/portfolio/accounts` | `portfolio.trading_accounts` |
| POST | `/v1/portfolio/accounts` | `portfolio.trading_accounts` |
| GET | `/v1/portfolio/summary` | `portfolio.valuation_snapshots`, `portfolio.position_snapshots` |
| GET | `/v1/portfolio/positions` | `portfolio.position_snapshots` |
| GET | `/v1/portfolio/positions/history` | `portfolio.position_snapshots` |
| GET | `/v1/portfolio/orders` | `portfolio.orders` |
| GET | `/v1/portfolio/orders/{order_id}` | `portfolio.orders`, `portfolio.order_events` |
| GET | `/v1/portfolio/trades` | `portfolio.trades` |

### `v0.6.3` - Portfolio action routes (manual only)
| Method | Route | Required tables |
|---|---|---|
| POST | `/v1/portfolio/orders` | `portfolio.orders`, `portfolio.order_events` |
| DELETE | `/v1/portfolio/orders/{order_id}` | `portfolio.orders`, `portfolio.order_events` |
| POST | `/v1/sync/polymarket/positions` | `portfolio.position_snapshots`, `core.sync_runs` |
| POST | `/v1/sync/polymarket/orders` | `portfolio.orders`, `portfolio.order_events`, `core.sync_runs` |
| POST | `/v1/sync/polymarket/trades` | `portfolio.trades`, `core.sync_runs` |
| POST | `/v1/sync/polymarket/orderbook` | `market_data.orderbook_snapshots`, `market_data.orderbook_levels`, `core.sync_runs` |
| POST | `/v1/sync/polymarket/prices` | `market_data.outcome_price_ticks`, `market_data.outcome_price_candles`, `core.sync_runs` |

### `v0.6.4` - Strategy metadata/control routes
| Method | Route | Required tables |
|---|---|---|
| GET | `/v1/strategy/types` | `strategy.strategy_types` |
| POST | `/v1/strategy/types` | `strategy.strategy_types` |
| GET | `/v1/strategy/definitions` | `strategy.strategy_definitions` |
| POST | `/v1/strategy/definitions` | `strategy.strategy_definitions` |
| GET | `/v1/strategy/instances` | `strategy.strategy_instances` |
| POST | `/v1/strategy/instances` | `strategy.strategy_instances` |
| PATCH | `/v1/strategy/instances/{strategy_instance_id}` | `strategy.strategy_instances` |
| POST | `/v1/strategy/instances/{strategy_instance_id}/targets` | `strategy.strategy_targets` |
| GET | `/v1/strategy/instances/{strategy_instance_id}/signals` | `strategy.strategy_signals` |
| POST | `/v1/strategy/instances/{strategy_instance_id}/signals` | `strategy.strategy_signals` |

### `v0.7.1` - NBA read routes
| Method | Route | Required tables |
|---|---|---|
| GET | `/v1/nba/games` | `nba.nba_games` |
| GET | `/v1/nba/games/{game_id}` | `nba.nba_games` |
| GET | `/v1/nba/games/{game_id}/event-links` | `nba.nba_game_event_links`, `catalog.events` |
| POST | `/v1/nba/games/{game_id}/event-links` | `nba.nba_game_event_links` |
| GET | `/v1/nba/teams` | `nba.nba_teams` |
| GET | `/v1/nba/teams/{team_id}/stats` | `nba.nba_team_stats_snapshots` |
| GET | `/v1/nba/teams/{team_id}/insights` | `nba.nba_team_insights` |
| GET | `/v1/nba/players` | `nba.nba_player_stats_snapshots` |

### `v0.7.3` - NBA live and context routes
| Method | Route | Required tables |
|---|---|---|
| POST | `/v1/sync/nba/live/{game_id}` | `nba.nba_live_game_snapshots`, `nba.nba_play_by_play`, `core.sync_runs` |
| GET | `/v1/nba/games/{game_id}/live` | `nba.nba_live_game_snapshots`, `nba.nba_play_by_play` |
| GET | `/v1/nba/games/{game_id}/context/pre` | `nba.nba_context_cache` or on-demand pipelines |
| GET | `/v1/nba/games/{game_id}/context/live` | `nba.nba_context_cache` or on-demand pipelines |

### `v0.8.3` - Chroma and documents routes
| Method | Route | Required tables |
|---|---|---|
| POST | `/v1/research/collections` | `research.event_collections` |
| GET | `/v1/research/collections` | `research.event_collections` |
| POST | `/v1/research/documents` | `research.event_documents` |
| POST | `/v1/research/documents/batch` | `research.event_documents` |
| GET | `/v1/events/{event_id}/documents` | `research.event_documents` |
| POST | `/v1/events/{event_id}/documents/query` | `research.event_documents` + Chroma backend |

### `v0.9.1` - Job orchestration and ops routes
| Method | Route | Required tables |
|---|---|---|
| GET | `/v1/jobs` | `ops.job_definitions` |
| POST | `/v1/jobs` | `ops.job_definitions` |
| POST | `/v1/jobs/{job_code}/run` | `ops.job_runs`, `core.sync_runs` |
| GET | `/v1/jobs/runs` | `ops.job_runs` |

## Compatibility aliases (temporary)
- `POST /place_order` -> `POST /v1/portfolio/orders`
- `GET /view_portfolio` -> `GET /v1/portfolio/positions`
- `GET /view_trade_history` -> `GET /v1/portfolio/trades`
- `POST /cancel_order` -> `DELETE /v1/portfolio/orders/{order_id}`
- `POST /add_event` -> `POST /v1/events/import-url`

## v1 endpoint completion checklist
v1 is reached when all route groups below are active and tested:
1. `v0.5.*` system + catalog + sync triggers
2. `v0.6.*` market data + portfolio + strategy metadata
3. `v0.7.*` NBA module + context delivery
4. `v0.8.*` Chroma/event-doc linkage
5. `v0.9.*` ops and production service controls

## Route governance rules
- Every new route must reference:
  - activation phase
  - required tables
  - required node methods
  - test script path
- For time-based sync/read routes, tests must explicitly validate past/current/future behavior (or document source-imposed limits).
- No route may be marked active if its dependencies are not complete in corresponding checkpoint file.

## Current source constraints (2026-02-17)
- Structure gate (`v0.2.7`-`v0.2.9`) is complete: provider-centric wrappers now exist under `app/providers/*`, canonical domain wrappers under `app/domain/events/canonical/*`, and ingestion wrappers under `app/ingestion/*`; upcoming route/service work in `v0.3+` should target these paths first.
- Canonical mapping pre-route layer is now validated in `app/data/pipelines/canonical/*` with fixture-backed integration tests (`tests/app/data/pipelines/canonical/*_pytest.py`); route groups stay phase-gated, but future `/v1/events/import-url` and `/v1/sync/*` routes should consume this canonical contract directly.
- Gamma events sync routes must use timezone-aware filters and split-window validation (past/future windows separately) due query-window sensitivity.
- Gamma events route logic should prefer `tag_slug=nba` and snake_case date params; broad windows still need client-side validation.
- Gamma moneyline route no longer hard-blocked at zero rows after query hardening, but must keep fallback path (`/events` nested markets) and client-side game-date filtering.
- Historical odds acquisition is validated via `app/data/nodes/polymarket/gamma/nba/odds_history_node.py` using CLOB `/prices-history` by token id, with interval fallback and snapshot fallback when direct history is empty.
- Fallback stream-to-history collection is validated via `app/data/nodes/polymarket/gamma/nba/fallback_stream_history_collector.py`, emitting append-only samples (`source=fallback_stream`) for windows where direct history under-returns.
- NBA play-by-play ingestion contract is validated in `app/data/nodes/nba/live/play_by_play.py` with deterministic normalization (`event_index`, score deltas) and idempotent sqlite upsert helper for pre-schema persistence checks.
- Polymarket orderbook polling contract is validated in `app/data/nodes/polymarket/blockchain/stream_orderbook.py` (`OrderbookStreamConfig` + `stream_orderbook`) and is ready for future sync route wiring.
